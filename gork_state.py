import os
import time
import random
import hashlib
from threading import Lock
from core.schemas import GorkConfig

# Default configuration (kept minimal reference from the main app)
_raw_default = {
    'base_bet_usd': 1.0,
    'die_last_base_bet_usd': 0.5, 'die_last_tp_usd': 10.0, 'die_last_sl_usd': -5.0, 'die_last_daily_loss_cap_usd': -20.0,
    'vanish_base_bet_usd': 0.5, 'vanish_tp_usd': 5.0, 'vanish_sl_usd': -3.0, 'vanish_daily_loss_cap_usd': -15.0,
    'eternal_base_bet_usd': 0.25, 'eternal_tp_usd': 5.0, 'eternal_sl_usd': -2.0, 'eternal_daily_loss_cap_usd': -10.0,
    'session_tp_usd': 10.0, 'session_sl_usd': -5.0, 'daily_loss_cap_usd': -10.0,
    'weekly_loss_cap_usd': -50.0, 'all_time_drawdown_cap_usd': -100.0,
    'min_bet_floor': 0.000001, 'enable_seed_rotation': True, 'enable_daily_lock': True,
    'active_currency': 'btc', 'gemini_api_key': '',
    'basic_bet_amount': 1.0, 'basic_on_win': 'reset', 'basic_win_mult': 1.0, 'basic_on_loss': 'multiply', 'basic_loss_mult': 2.0, 'basic_target': 50.50, 'basic_condition': 'over',
    'rm_base_bet_usd': 0.5, 'rm_tp_usd': 5.0, 'rm_sl_usd': -10.0, 'rm_daily_loss_cap_usd': -20.0,
    'wg99_base_bet_usd': 1.0, 'wg99_tp_usd': 2.0, 'wg99_sl_usd': -10.0, 'wg99_daily_loss_cap_usd': -30.0,
    'fib_base_bet_usd': 0.5, 'fib_tp_usd': 5.0, 'fib_sl_usd': -10.0, 'fib_daily_loss_cap_usd': -20.0, 'fib_win_chance': 49.50,
    'par_base_bet_usd': 0.25, 'par_tp_usd': 10.0, 'par_sl_usd': -5.0, 'par_daily_loss_cap_usd': -15.0, 'par_win_chance': 49.50, 'par_streak_target': 3,
    'osc_base_bet_usd': 0.5, 'osc_tp_usd': 5.0, 'osc_sl_usd': -10.0, 'osc_daily_loss_cap_usd': -20.0, 'osc_win_chance': 49.50,
    'dc_difficulty': 'easy', 'dc_target_col': 0
}
DEFAULT_CONFIG = GorkConfig(**_raw_default).dict()

# Shared runtime state for the application and terminal
state_lock = Lock()
state = {
    'config': DEFAULT_CONFIG.copy(), 'strategy': 'the_gork', 'balance': {'available': 1000.0, 'currency': 'btc'},
    'is_running': False, 'current_bet': 0.01, 'daily_start_balance': 1000.0, 'daily_start_time': time.time(),
    'peak_balance': 1000.0, 'recent_outcomes': [], 'current_win_streak': 0, 'current_lose_streak': 0,
    'total_bets': 1000000, 'total_wagered': 0.0, 'logs': [], 'roll_history': [],
    'prices': {'btc': 100000.0, 'ltc': 100.0, 'eth': 2500.0},
    'server_seed_hash': hashlib.sha256(os.urandom(32)).hexdigest(),
    'client_seed': f"gork-{random.randint(1000,9999)}", 'nonce': 0
}
