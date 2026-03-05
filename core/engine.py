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
        currency = self.state['balance']['currency'].lower()
        price = self.state['prices'].get(currency, 1.0)
        bal_usd = bal * price

        if now - self.state['daily_start_time'] > 84600:
            self.state['daily_start_balance'] = bal
            self.state['daily_start_time'] = now

        strat = self.state['strategy']
        map_daily = {
            'the_gork': 'daily_loss_cap_usd',
            'die_last': 'die_last_daily_loss_cap_usd',
            'vanish_in_volume': 'vanish_daily_loss_cap_usd',
            'eternal_volume': 'eternal_daily_loss_cap_usd',
            'reverted_martingale': 'rm_daily_loss_cap_usd',
            'wager_grind_99': 'wg99_daily_loss_cap_usd',
            'fibonacci': 'fib_daily_loss_cap_usd',
            'paroli': 'par_daily_loss_cap_usd',
            'oscars_grind': 'osc_daily_loss_cap_usd'
        }
        
        daily_cap_usd = cfg.get(map_daily.get(strat, 'daily_loss_cap_usd'), -10.0)
        if strat == 'custom': daily_cap_usd = cfg.get('c_daily_usd', -5.0)
        
        all_time_cap_usd = cfg.get('all_time_drawdown_cap_usd', -100.0)

        if cfg.get('enable_daily_lock', True):
            daily_diff_usd = (bal - self.state['daily_start_balance']) * price
            if daily_diff_usd <= daily_cap_usd:
                return True, f"Daily cap ${daily_diff_usd:.2f}"
        
        if cfg.get('enable_alltime_lock', True):
            dd_diff_usd = (bal - self.state['peak_balance']) * price
            if dd_diff_usd <= all_time_cap_usd:
                return True, f"All-time drawdown ${dd_diff_usd:.2f}"
                
        return False, ""

    def usd_to_coin(self, usd_amount):
        currency = self.state['balance']['currency'].lower()
        price = self.state['prices'].get(currency, 1.0)
        if price <= 0: return 0.000001
        return usd_amount / price

    def calculate_ema_cross_bet(self, balance):
        cfg = self.state['config']
        base_bet_usd = 1.0 # Default
        target = 50.50
        condition = "over"
        
        if not self.state.get('chart_data') or len(self.state['chart_data']) < 20:
            bet_coin = self.usd_to_coin(base_bet_usd)
            return max(cfg.get('min_bet_floor', 0.000001), bet_coin), condition, target

        ema5 = self.state['chart_data'][-1].get('ema5', 0)
        ema20 = self.state['chart_data'][-1].get('ema20', 0)
        
        recent_wins = sum(1 for w in self.state['recent_outcomes'][-14:] if w)
        rsi_proxy = (recent_wins / 14.0) * 100 if len(self.state['recent_outcomes']) >= 14 else 50
        
        condition = "over" if ema5 > ema20 else "under"
        bet_usd = base_bet_usd
        if rsi_proxy > 70: bet_usd *= 0.25
        elif rsi_proxy < 30: bet_usd *= 1.5
            
        bet_coin = self.usd_to_coin(bet_usd)
        return max(cfg.get('min_bet_floor', 0.000001), min(bet_coin, balance * 0.02)), condition, target

    def calculate_die_last_bet(self, balance):
        cfg = self.state['config']
        base_bet_usd = cfg.get('die_last_base_bet_usd', 0.5)
        streak = self.state['current_win_streak']
        mult = 0.5
        if streak == 1: mult = 1.0
        elif streak == 2: mult = 1.5
        elif streak == 3: mult = 2.0
        elif streak >= 4: mult = 2.5
        
        if len(self.state['recent_outcomes']) >= 10:
            if sum(1 for won in self.state['recent_outcomes'][-10:] if not won) >= 6:
                mult *= 0.5
        
        bet_coin = self.usd_to_coin(base_bet_usd * mult)
        return max(cfg.get('min_bet_floor', 0.000001), min(bet_coin, balance * 0.01))

    def calculate_eternal_volume_bet(self, balance):
        cfg = self.state['config']
        base_bet_usd = cfg.get('eternal_base_bet_usd', 0.25)
        bet_coin = self.usd_to_coin(base_bet_usd)
        return max(cfg.get('min_bet_floor', 0.000001), min(bet_coin, balance * 0.002))

    def calculate_vanish_bet(self, balance):
        cfg = self.state['config']
        base_bet_usd = cfg.get('vanish_base_bet_usd', 0.5)
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
        
        bet_coin = self.usd_to_coin(base_bet_usd * shrink * mult * circuit)
        return max(cfg.get('min_bet_floor', 0.000001), min(bet_coin, balance * 0.003))

    def update_state(self, won, roll, bet, multiplier):
        """Called by simulator and real-time loop after a roll is resolved."""
        strat = self.state['strategy']
        
        # Base updates
        if won:
            self.state['current_win_streak'] += 1
            self.state['current_lose_streak'] = 0
            self.state['balance']['available'] += (bet * multiplier)
        else:
            self.state['current_lose_streak'] += 1
            self.state['current_win_streak'] = 0
            self.state['balance']['available'] -= bet
            
        if self.state['balance']['available'] > self.state['peak_balance']:
            self.state['peak_balance'] = self.state['balance']['available']

        # Strategy-specific updates
        if strat == 'basic':
            cfg = self.state['config']
            if won:
                action = cfg.get('basic_on_win', 'reset')
                if action == 'reset':
                    self.state['basic_current_bet_usd'] = float(cfg.get('basic_bet_amount', 1.0))
                elif action == 'multiply':
                    self.state['basic_current_bet_usd'] = self.state.get('basic_current_bet_usd', 1.0) * float(cfg.get('basic_win_mult', 1.0))
            else:
                action = cfg.get('basic_on_loss', 'multiply')
                if action == 'reset':
                    self.state['basic_current_bet_usd'] = float(cfg.get('basic_bet_amount', 1.0))
                elif action == 'multiply':
                    self.state['basic_current_bet_usd'] = self.state.get('basic_current_bet_usd', 1.0) * float(cfg.get('basic_loss_mult', 2.0))

        elif strat == 'martingale':
            cfg = self.state['config']
            base_usd = float(cfg.get('martingale_base_usd', 1.0))
            mult = float(cfg.get('martingale_mult', 2.0))
            if won:
                self.state['martingale_current_usd'] = base_usd
            else:
                current = self.state.get('martingale_current_usd', base_usd)
                self.state['martingale_current_usd'] = current * mult

        elif strat == 'dalembert':
            cfg = self.state['config']
            base_usd = float(cfg.get('dalembert_base_usd', 1.0))
            if won:
                current = self.state.get('dalembert_current_usd', base_usd)
                self.state['dalembert_current_usd'] = max(base_usd, current - base_usd)
            else:
                current = self.state.get('dalembert_current_usd', base_usd)
                self.state['dalembert_current_usd'] = current + base_usd

        elif strat == 'labouchere':
            cfg = self.state['config']
            base_usd = float(cfg.get('labouchere_base_usd', 1.0))
            seq = self.state.get('lab_sequence', [])
            
            if not seq: 
                # Create default sequence 1, 2, 3
                seq = [base_usd, base_usd * 2, base_usd * 3]
                
            if won:
                if len(seq) > 0: seq.pop(0)
                if len(seq) > 0: seq.pop()
            else:
                bet_usd = seq[0] + seq[-1] if len(seq) > 1 else (seq[0] if seq else base_usd)
                seq.append(bet_usd)
                
            if not seq: # Reset if list is cleared (cycle completed)
                seq = [base_usd, base_usd * 2, base_usd * 3]
                
            self.state['lab_sequence'] = seq

    def calculate_gork_bet(self, balance):
        cfg = self.state['config']
        start_bal = self.state.get('daily_start_balance', balance)
        distance = start_bal - balance
        
        base_bet_usd = cfg.get('the_gork_base_usd', 1.0)
        max_recovery_usd = base_bet_usd * 10.0 # Don't go crazy
        
        if distance <= 0:
            target_usd = base_bet_usd
        else:
            # Need to recover distance. Bet enough to win back the distance
            # If target is 2.0x (50%), we need to bet `distance` to win `distance` profit
            target_usd = min(max_recovery_usd, (distance * 1.05) + base_bet_usd)
            
        bet_coin = self.usd_to_coin(target_usd)
        return max(cfg.get('min_bet_floor', 0.000001), min(bet_coin, balance * 0.1)), "over", 50.50

    def calculate_martingale_bet(self, balance):
        cfg = self.state['config']
        current_usd = self.state.get('martingale_current_usd', cfg.get('martingale_base_usd', 1.0))
        max_usd = cfg.get('martingale_max_usd', 100.0)
        bet_usd = min(current_usd, max_usd)
        bet_coin = self.usd_to_coin(bet_usd)
        return max(cfg.get('min_bet_floor', 0.000001), min(bet_coin, balance * 0.5)), "over", cfg.get('martingale_target', 50.50)

    def calculate_dalembert_bet(self, balance):
        cfg = self.state['config']
        current_usd = self.state.get('dalembert_current_usd', cfg.get('dalembert_base_usd', 1.0))
        bet_coin = self.usd_to_coin(current_usd)
        return max(cfg.get('min_bet_floor', 0.000001), min(bet_coin, balance * 0.5)), "over", cfg.get('dalembert_target', 50.50)

    def calculate_labouchere_bet(self, balance):
        cfg = self.state['config']
        base_usd = float(cfg.get('labouchere_base_usd', 1.0))
        seq = self.state.get('lab_sequence', [])
        if not seq: 
            seq = [base_usd, base_usd * 2, base_usd * 3]
            self.state['lab_sequence'] = seq
            
        bet_usd = seq[0] + seq[-1] if len(seq) > 1 else (seq[0] if seq else base_usd)
        bet_coin = self.usd_to_coin(bet_usd)
        return max(cfg.get('min_bet_floor', 0.000001), min(bet_coin, balance * 0.5)), "over", cfg.get('labouchere_target', 50.50)

    def calculate_basic_bet(self, balance):
        cfg = self.state['config']
        if self.state.get('basic_current_bet_usd', 0) <= 0:
            self.state['basic_current_bet_usd'] = cfg.get('basic_bet_amount', 1.0)
        
        bet_coin = self.usd_to_coin(self.state['basic_current_bet_usd'])
        return max(cfg.get('min_bet_floor', 0.000001), bet_coin), cfg.get('basic_condition', 'over'), cfg.get('basic_target', 50.50)

    def calculate_bet(self, strategy, balance):
        if strategy == 'the_gork': return self.calculate_gork_bet(balance)
        if strategy == 'martingale': return self.calculate_martingale_bet(balance)
        if strategy == 'dalembert': return self.calculate_dalembert_bet(balance)
        if strategy == 'labouchere': return self.calculate_labouchere_bet(balance)
        if strategy == 'basic': return self.calculate_basic_bet(balance)
        # Default fallback
        return self.usd_to_coin(1.0), "over", 50.50


