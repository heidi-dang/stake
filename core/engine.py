import time
import random
import logging
from .utils import calculate_ema

logger = logging.getLogger("GorkEngine")

class GorkEngine:
    def __init__(self, state):
        self.state = state

    def is_locked(self):
        cfg = self.state['config']
        now = time.time()
        bal = self.state['balance']['available']

        if now - self.state['daily_start_time'] > 84600:
            self.state['daily_start_balance'] = bal
            self.state['daily_start_time'] = now

        strat = self.state['strategy']
        map_daily = {
            'the_gork': 'daily_loss_cap_pct',
            'die_last': 'die_last_daily_loss_cap_pct',
            'vanish_in_volume': 'vanish_daily_loss_cap_pct',
            'eternal_volume': 'eternal_daily_loss_cap_pct',
            'reverted_martingale': 'rm_daily_loss_cap_pct',
            'wager_grind_99': 'wg99_daily_loss_cap_pct',
            'fibonacci': 'fib_daily_loss_cap_pct',
            'paroli': 'par_daily_loss_cap_pct',
            'oscars_grind': 'osc_daily_loss_cap_pct'
        }
        
        daily_cap = cfg.get(map_daily.get(strat, 'daily_loss_cap_pct'), -2.5)
        if strat == 'custom': daily_cap = cfg.get('c_daily', -1.0)
        
        all_time_cap = cfg.get('all_time_drawdown_cap_pct', -8.0)

        if cfg.get('enable_daily_lock', True):
            daily_pct = ((bal - self.state['daily_start_balance']) / self.state['daily_start_balance'] * 100) if self.state['daily_start_balance'] > 0 else 0
            if daily_pct <= daily_cap:
                return True, f"Daily cap {daily_pct:.2f}%"
        
        if cfg.get('enable_alltime_lock', True):
            dd_pct = ((bal - self.state['peak_balance']) / self.state['peak_balance'] * 100) if self.state['peak_balance'] > 0 else 0
            if dd_pct <= all_time_cap:
                return True, f"All-time drawdown {dd_pct:.2f}%"
                
        return False, ""

    def calculate_ema_cross_bet(self, balance):
        cfg = self.state['config']
        base_bet = balance * 0.002
        target = 50.50
        condition = "over"
        
        if not self.state.get('chart_data') or len(self.state['chart_data']) < 20:
            return max(cfg.get('min_bet_floor', 0.000001), base_bet), condition, target

        ema5 = self.state['chart_data'][-1].get('ema5', 0)
        ema20 = self.state['chart_data'][-1].get('ema20', 0)
        
        recent_wins = sum(1 for w in self.state['recent_outcomes'][-14:] if w)
        rsi_proxy = (recent_wins / 14.0) * 100 if len(self.state['recent_outcomes']) >= 14 else 50
        
        condition = "over" if ema5 > ema20 else "under"
        bet = base_bet
        if rsi_proxy > 70: bet *= 0.25
        elif rsi_proxy < 30: bet *= 1.5
            
        return max(cfg.get('min_bet_floor', 0.000001), min(bet, balance * 0.02)), condition, target

    def calculate_die_last_bet(self, balance):
        cfg = self.state['config']
        base_bet = balance * cfg.get('die_last_base_bet_pct', 0.005)
        streak = self.state['current_win_streak']
        mult = 0.5
        if streak == 1: mult = 1.0
        elif streak == 2: mult = 1.5
        elif streak == 3: mult = 2.0
        elif streak >= 4: mult = 2.5
        
        if len(self.state['recent_outcomes']) >= 10:
            if sum(1 for won in self.state['recent_outcomes'][-10:] if not won) >= 6:
                mult *= 0.5
        return max(cfg.get('min_bet_floor', 0.000001), min(base_bet * mult, balance * 0.01))

    def calculate_eternal_volume_bet(self, balance):
        cfg = self.state['config']
        base_bet = balance * cfg.get('eternal_base_bet_pct', 0.0012)
        return max(cfg.get('min_bet_floor', 0.000001), min(base_bet, balance * 0.002))

    def calculate_vanish_bet(self, balance):
        cfg = self.state['config']
        base_bet = balance * cfg.get('vanish_base_bet_pct', 0.0015)
        start_bal = self.state.get('daily_start_balance', balance)
        dd_pct = (balance - start_bal) / start_bal * 100 if start_bal > 0 else 0
        shrink = 1.0
        if dd_pct <= -7.0: shrink = 0.4
        elif dd_pct <= -5.0: shrink = 0.6
        elif dd_pct <= -3.0: shrink = 0.8
            
        streak = self.state['current_win_streak']
        mults = [0.6, 0.9, 1.2, 1.5, 1.8]
        mult = mults[min(streak, 4)]
        
        circuit = 1.0
        if len(self.state['recent_outcomes']) >= 8:
            if sum(1 for won in self.state['recent_outcomes'][-8:] if not won) >= 4:
                circuit = 0.4
        return max(cfg.get('min_bet_floor', 0.000001), min(base_bet * shrink * mult * circuit, balance * 0.003))

    def calculate_gork_bet(self, balance):
        cfg = self.state['config']
        distance = balance - self.state.get('daily_start_balance', balance)
        max_bet = balance * cfg.get('base_bet_pct', 0.0012)
        recovery = abs(distance) * 1.2 if distance < 0 else 0
        bet = min(max_bet, recovery)
        return max(cfg.get('min_bet_floor', 0.000001), min(bet, balance * 0.003))

    def calculate_basic_bet(self, balance):
        cfg = self.state['config']
        if self.state.get('basic_current_bet', 0) <= 0:
            self.state['basic_current_bet'] = cfg.get('basic_bet_amount', 0.000001)
        return max(cfg.get('min_bet_floor', 0.000001), self.state['basic_current_bet']), cfg.get('basic_condition', 'over'), cfg.get('basic_target', 50.50)

    def calculate_reverted_martingale_bet(self, balance):
        cfg = self.state['config']
        base_bet = balance * cfg.get('rm_base_bet_pct', 0.0012)
        start_bal = self.state.get('daily_start_balance', balance)
        session_pct = ((balance - start_bal) / start_bal * 100) if start_bal > 0 else 0
        target = 50.50
        if session_pct < -0.5: target = 60.50
        if session_pct < -1.0: target = 70.50
        factor = max(0.1, 1.0 - (session_pct / cfg.get('session_tp_pct', 3.0)))
        if session_pct < 0:
            dd_factor = abs(session_pct) / abs(cfg.get('session_sl_pct', -1.4))
            factor = 1.0 + (min(dd_factor, 1.0) * 2.0)
        return max(cfg.get('min_bet_floor', 0.000001), min(base_bet * factor, balance * 0.05)), "over", target

    def calculate_wager_grind_99(self, balance):
        cfg = self.state['config']
        base_bet = balance * cfg.get('wg99_base_bet_pct', 0.05)
        return max(cfg.get('min_bet_floor', 0.000001), min(base_bet, balance * 0.1)), "over", 1.00

    def calculate_fibonacci_bet(self, balance):
        cfg = self.state['config']
        base_bet = balance * cfg.get('fib_base_bet_pct', 0.001)
        def fib(n):
            if n <= 1: return 1
            a, b = 1, 1
            for _ in range(2, n + 1): a, b = b, a + b
            return b
        multiplier = fib(self.state.get('fib_index', 0))
        return max(cfg.get('min_bet_floor', 0.000001), min(base_bet * multiplier, balance * 0.1)), "over", 100.0 - cfg.get('fib_win_chance', 49.50)

    def calculate_paroli_bet(self, balance):
        cfg = self.state['config']
        base_bet = balance * cfg.get('par_base_bet_pct', 0.0005)
        mult = 2 ** self.state.get('par_streak', 0) if self.state.get('par_streak', 0) > 0 else 1
        return max(cfg.get('min_bet_floor', 0.000001), min(base_bet * mult, balance * 0.1)), "over", 100.0 - cfg.get('par_win_chance', 49.50)

    def calculate_oscars_grind_bet(self, balance):
        cfg = self.state['config']
        base_unit = balance * cfg.get('osc_base_bet_pct', 0.001)
        bet = base_unit * self.state.get('osc_current_unit', 1)
        profit_needed = base_unit - self.state.get('osc_session_profit', 0)
        if profit_needed > 0 and bet > profit_needed: bet = profit_needed
        if bet <= 0: bet = base_unit
        return max(cfg.get('min_bet_floor', 0.000001), min(bet, balance * 0.1)), "over", 100.0 - cfg.get('osc_win_chance', 49.50)

    def calculate_bet(self, strategy, balance):
        if strategy == 'the_gork': return self.calculate_gork_bet(balance), "over", 50.50
        if strategy == 'die_last': return self.calculate_die_last_bet(balance), "over", 50.50
        if strategy == 'ema_cross': return self.calculate_ema_cross_bet(balance)
        if strategy == 'vanish_in_volume': return self.calculate_vanish_bet(balance), "over", 50.50
        if strategy == 'eternal_volume': return self.calculate_eternal_volume_bet(balance), "over", 50.50
        if strategy == 'reverted_martingale': return self.calculate_reverted_martingale_bet(balance)
        if strategy == 'wager_grind_99': return self.calculate_wager_grind_99(balance)
        if strategy == 'fibonacci': return self.calculate_fibonacci_bet(balance)
        if strategy == 'paroli': return self.calculate_paroli_bet(balance)
        if strategy == 'oscars_grind': return self.calculate_oscars_grind_bet(balance)
        if strategy == 'basic': return self.calculate_basic_bet(balance)
        # Custom logic would be handled by the engine caller or injected
        return balance * 0.0001, "over", 50.50
