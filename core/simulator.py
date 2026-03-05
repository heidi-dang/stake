import os
import random
import hashlib
import logging
from .utils import stake_derive_roll

logger = logging.getLogger("GorkSimulator")

def run_simulation_internal(data, engine_class):
    strat = data.get('strategy', 'the_gork')
    start_bal = float(data.get('starting_balance', 1000.0))
    bets_to_sim = int(data.get('bets_to_simulate', 10000))
    
    # Simulation specific state
    sim_state = {
        'config': data.copy(),
        'strategy': strat,
        'balance': {'available': start_bal, 'currency': 'usd'},
        'prices': {'usd': 1.0, 'btc': 1.0, 'eth': 1.0, 'ltc': 1.0},
        'daily_start_balance': start_bal,
        'daily_start_time': 0,
        'peak_balance': start_bal,
        'current_win_streak': 0,
        'current_lose_streak': 0,
        'recent_outcomes': [],
        'roll_history': [],
        'chart_data': []
    }
    
    engine = engine_class(sim_state)
    
    sim_bal = start_bal
    sim_peak = start_bal
    sim_worst_dd = 0.0
    sim_wagered = 0.0
    wins = 0
    losses = 0
    cb_hits = 0
    equity_curve = []
    sim_bet_count = 0
    snapshot_every = max(1, bets_to_sim // 300)
    
    sim_nonce = 0
    sim_server_seed = hashlib.sha256(os.urandom(32)).hexdigest()
    sim_client_seed = f"sim-{random.randint(100000,999999)}"
    
    for _ in range(bets_to_sim):
        if sim_bal <= max(data.get('min_bet_floor', 0.000001) * 2, 0.000001):
            break
            
        sim_bet_count += 1
        sim_nonce += 1
        
        if sim_bet_count % 500 == 0:
            sim_server_seed = hashlib.sha256(os.urandom(32)).hexdigest()
            sim_nonce = 1
            
        if sim_bet_count % snapshot_every == 0:
            equity_curve.append({'time': sim_bet_count, 'value': round(sim_bal, 8), 'pnl': round(sim_bal - start_bal, 8)})
            
        # Strategy Execution
        sim_state['balance']['available'] = sim_bal
        bet, condition, target = engine.calculate_bet(strat, sim_bal)
        
        bet = min(bet, sim_bal)
        if bet < data.get('min_bet_floor', 0.000001): bet = data.get('min_bet_floor', 0.000001)
        
        sim_wagered += bet
        sim_bal -= bet
        
        roll = stake_derive_roll(sim_server_seed, sim_client_seed, sim_nonce)
        won = roll > target if condition == "over" else roll < target
        
        # Update state for next iteration
        sim_state['recent_outcomes'].append(won)
        if len(sim_state['recent_outcomes']) > 20: sim_state['recent_outcomes'].pop(0)
        sim_state['roll_history'].append(roll)
        if len(sim_state['roll_history']) > 100: sim_state['roll_history'].pop(0)
        
        if won:
            win_chance = (100.0 - target) if condition == 'over' else target
            multiplier = (100.0 / win_chance) * 0.99
            sim_bal += (bet * multiplier)
            wins += 1
            sim_state['current_win_streak'] += 1
            sim_state['current_lose_streak'] = 0
            
            if strat == 'basic':
                action = data.get('basic_on_win', 'reset')
                if action == 'reset': sim_state['basic_current_bet'] = float(data.get('basic_bet_amount', 0.0001))
                elif action == 'multiply': sim_state.get('basic_current_bet', 0) * float(data.get('basic_win_mult', 1.0))
        else:
            losses += 1
            sim_state['current_win_streak'] = 0
            sim_state['current_lose_streak'] += 1
            
            if strat == 'basic':
                action = data.get('basic_on_loss', 'multiply')
                if action == 'reset': sim_state['basic_current_bet'] = float(data.get('basic_bet_amount', 0.0001))
                elif action == 'multiply': sim_state['basic_current_bet'] = sim_state.get('basic_current_bet', 0.0001) * float(data.get('basic_loss_mult', 2.0))
        
        if sim_bal > sim_peak:
            sim_peak = sim_bal
            sim_state['peak_balance'] = sim_peak
            
        dd_pct = (sim_bal - sim_peak) / sim_peak * 100 if sim_peak > 0 else 0
        if dd_pct < sim_worst_dd: sim_worst_dd = dd_pct
        
        # Check for Session TP/SL (USD-based)
        tp_usd = data.get(f"{strat}_tp_usd", data.get('session_tp_usd', 10.0))
        sl_usd = data.get(f"{strat}_sl_usd", data.get('session_sl_usd', -5.0))
        sess_pnl = sim_bal - sim_state['daily_start_balance']
        
        if sess_pnl >= tp_usd or sess_pnl <= sl_usd:
            cb_hits += 1
            sim_state['daily_start_balance'] = sim_bal
            sim_state['current_win_streak'] = 0
            
    equity_curve.append({'time': sim_bet_count, 'value': round(sim_bal, 8), 'pnl': round(sim_bal - start_bal, 8)})
    
    return {
        'starting_balance': start_bal,
        'final_balance': sim_bal,
        'peak_balance': sim_peak,
        'worst_drawdown': sim_worst_dd,
        'total_wagered': sim_wagered,
        'wins': wins,
        'losses': losses,
        'circuit_breakers': cb_hits,
        'equity_curve': equity_curve
    }
