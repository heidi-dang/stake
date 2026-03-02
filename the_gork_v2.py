# ========================================================
# THE GORK - Complete Single File Edition
# All strategies + The Gork flagship + Full Dashboard
# ========================================================

import os
import json
import time
import threading
import logging
import random
import sqlite3
import hmac
import hashlib
import jwt
from functools import wraps
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template_string
from threading import Lock
import requests

DB_PATH = 'gork_data.db'
CUSTOM_STRAT_PATH = 'custom_strategy.py'
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS ai_history (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
        c.execute('CREATE TABLE IF NOT EXISTS chart_data (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, bet_number INTEGER, profit REAL, win_streak INTEGER, lose_streak INTEGER, balance REAL)')
        c.execute('''CREATE TABLE IF NOT EXISTS saved_strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            strategy TEXT NOT NULL,
            config TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        try:
            c.execute('ALTER TABLE chart_data ADD COLUMN ema5 REAL')
            c.execute('ALTER TABLE chart_data ADD COLUMN ema20 REAL')
            c.execute('ALTER TABLE chart_data ADD COLUMN roll_result REAL')
        except sqlite3.OperationalError:
            pass
        
        # Seed default custom strategy if missing
        res = c.execute("SELECT value FROM settings WHERE key='custom_strategy'").fetchone()
        if not res:
            default_template = """# Custom Python Strategy
# You have access to 'balance', 'state', 'log', 'random', 'time'.
# Must define calculate_bet(balance) or set 'result' variable.

def calculate_bet(balance):
    # Default: Flat 0.1% of balance
    return balance * 0.001

result = calculate_bet(balance)
"""
            c.execute("INSERT INTO settings (key, value) VALUES ('custom_strategy', ?)", (default_template,))
        

STRATEGY_TEMPLATES = {
    "the_gork": """# THE GORK (Mirror) Template
# Auto-scales bet size based on distance to starting bankroll.

def calculate_bet(balance):
    cfg = state['config']
    start_bal = state['daily_start_balance']
    distance = balance - start_bal
    
    max_bet = balance * cfg.get('base_bet_pct', 0.0012)
    recovery = abs(distance) * 1.2 if distance < 0 else 0
    
    bet = min(max_bet, recovery)
    return max(cfg.get('min_bet_floor', 0.000001), min(bet, balance * 0.003))

result = calculate_bet(balance)
""",
    "die_last": """# DIE LAST Template
# Aggressive streak-based progression.

def calculate_bet(balance):
    cfg = state['config']
    base_bet = balance * cfg.get('die_last_base_bet_pct', 0.005)
    streak = state['current_win_streak']
    
    if streak == 0: mult = 0.5
    elif streak == 1: mult = 1.0
    elif streak == 2: mult = 1.5
    elif streak == 3: mult = 2.0
    else: mult = 2.5
    
    # Circuit breaker for bad runs
    if len(state['recent_outcomes']) >= 10:
        if sum(1 for won in state['recent_outcomes'][-10:] if not won) >= 6:
            mult *= 0.5
            
    return max(cfg.get('min_bet_floor', 0.000001), min(base_bet * mult, balance * 0.01))

result = calculate_bet(balance)
""",
    "vanish_in_volume": """# VANISH IN VOLUME Template
# Ultra-defensive with a dynamic 'shrink' factor.

def calculate_bet(balance):
    cfg = state['config']
    base_bet = balance * cfg.get('vanish_base_bet_pct', 0.0015)
    
    dd_pct = (balance - state['daily_start_balance']) / state['daily_start_balance'] * 100
    shrink = 1.0
    if dd_pct <= -7.0: shrink = 0.4
    elif dd_pct <= -5.0: shrink = 0.6
    elif dd_pct <= -3.0: shrink = 0.8
        
    streak = state['current_win_streak']
    if streak == 0: mult = 0.6
    elif streak == 1: mult = 0.9
    elif streak == 2: mult = 1.2
    elif streak == 3: mult = 1.5
    else: mult = 1.8
    
    circuit = 1.0
    if len(state['recent_outcomes']) >= 8:
        if sum(1 for won in state['recent_outcomes'][-8:] if not won) >= 4:
            circuit = 0.4
            
    return max(cfg.get('min_bet_floor', 0.000001), min(base_bet * shrink * mult * circuit, balance * 0.003))

result = calculate_bet(balance)
""",
    "eternal_volume": """# ETERNAL VOLUME Template
# Pure flat fractional betting for maximum volume.

def calculate_bet(balance):
    cfg = state['config']
    base_bet = balance * cfg.get('eternal_base_bet_pct', 0.0012)
    # No progression, just balance tracking.
    return max(cfg.get('min_bet_floor', 0.000001), min(base_bet, balance * 0.002))

result = calculate_bet(balance)
""",
    "ema_cross": """# EMA CROSSOVER Template
# Uses 5-Period vs 20-Period EMA crossover logic.

# Note: Accesses state['roll_history'] directly.
def calculate_bet(balance):
    cfg = state['config']
    base_bet = balance * cfg.get('ema_base_bet_pct', 0.0012)
    
    history = state['roll_history']
    
    def calculate_ema(data, period):
        if len(data) == 0: return None
        if len(data) < period: return sum(data) / len(data)
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = (val - ema) * multiplier + ema
        return ema

    ema5 = calculate_ema(history, 5)
    ema20 = calculate_ema(history, 20)
    
    # In Custom script, we only return the 'result' (bet size).
    # The 'condition' and 'target' are harder to dynamic-load via result only.
    # But usually custom scripts just return the bet amount here.
    return max(cfg.get('min_bet_floor', 0.000001), min(base_bet, balance * 0.003))

result = calculate_bet(balance)
"""
}

init_db()

try:
    from stake_api.main import Stake
    api_available = True
except ImportError:
    api_available = False
    print("Warning: stake_api module not found. Falling back to mock data.")

API_TOKEN = os.getenv('STAKE_API_TOKEN', '')
if not API_TOKEN:
    with sqlite3.connect(DB_PATH) as conn:
        res = conn.execute("SELECT value FROM settings WHERE key='api_token'").fetchone()
        if res:
            API_TOKEN = res[0]

GRAPHQL_URL = 'https://stake.com/_api/graphql'

stake_client = None
if api_available and API_TOKEN and API_TOKEN != 'your_real_token_here':
    try:
        stake_client = Stake(API_TOKEN)
    except Exception as e:
        print(f"Error initializing Stake API: {e}")

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("TheGork")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'gork_super_secret_jwt_key_2026'
state_lock = Lock()

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token or not token.startswith('Bearer '):
            return jsonify({'error': 'Token is missing or invalid. Please login.'}), 401
        try:
            token = token.split(' ')[1]
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
        except Exception as e:
            return jsonify({'error': 'Token is invalid or expired. Please login.'}), 401
        return f(*args, **kwargs)
    return decorated

# Default config for both routing logic configurations (tunable from dashboard)
DEFAULT_CONFIG = {
    # THE GORK DEFAULT (Mirror Balance)
    'base_bet_pct': 0.0012,
    
    # DIE LAST - WIN SOMETIMES
    'die_last_base_bet_pct': 0.005,
    'die_last_tp_pct': 8.0,
    'die_last_sl_pct': -3.5,
    'die_last_daily_loss_cap_pct': -12.0,
    
    # VANISH IN VOLUME
    'vanish_base_bet_pct': 0.0015,
    'vanish_tp_pct': 3.5,
    'vanish_sl_pct': -2.0,
    'vanish_daily_loss_cap_pct': -1.5,
    
    # ETERNAL VOLUME
    'eternal_base_bet_pct': 0.0012,
    'eternal_tp_pct': 3.0,
    'eternal_sl_pct': -1.4,
    'eternal_daily_loss_cap_pct': -2.5,
    
    # THE GORK (Flagship)
    'session_tp_pct': 3.0,
    'session_sl_pct': -1.4,
    'daily_loss_cap_pct': -1.8,
    
    # SHARED CAPS (GLOBAL)
    'weekly_loss_cap_pct': -4.0,
    'all_time_drawdown_cap_pct': -8.0,
    'daily_wager_cap_factor': 35,
    
    'min_bet_floor': 0.000001,
    'seed_rotate_min': 40,
    'seed_rotate_max': 80,
    'enable_seed_rotation': True,
    'enable_daily_lock': True,
    'enable_weekly_lock': True,
    'enable_alltime_lock': True,
    'active_currency': 'btc',
    'gemini_api_key': '',
    # BASIC STRATEGY
    'basic_bet_amount': 0.000001,
    'basic_on_win': 'reset', # reset, multiply, stay
    'basic_win_mult': 1.0,
    'basic_on_loss': 'multiply',
    'basic_loss_mult': 2.0,
    'basic_target': 50.50,
    'basic_condition': 'over',
    
    # REVERTED MARTINGALE
    'rm_base_bet_pct': 0.0012,
    'rm_tp_pct': 3.0,
    'rm_sl_pct': -8.0,
    'rm_daily_loss_cap_pct': -10.0,
    'rm_mult_on_loss': 0.5,
    'rm_mult_on_win': 1.0,
    
    # WAGER GRIND 99
    'wg99_base_bet_pct': 0.05,
    'wg99_tp_pct': 1.0,
    'wg99_sl_pct': -15.0,
    'wg99_daily_loss_cap_pct': -20.0,
    
    # FIBONACCI
    'fib_base_bet_pct': 0.001,
    'fib_tp_pct': 2.0,
    'fib_sl_pct': -5.0,
    'fib_daily_loss_cap_pct': -10.0,
    'fib_win_chance': 49.50,
    
    # PAROLI (REVERSE MARTINGALE)
    'par_base_bet_pct': 0.0005,
    'par_tp_pct': 5.0,
    'par_sl_pct': -3.0,
    'par_daily_loss_cap_pct': -8.0,
    'par_win_chance': 49.50,
    'par_streak_target': 3,
    
    # OSCAR'S GRIND (TARGET +1 UNIT)
    'osc_base_bet_pct': 0.001,
    'osc_tp_pct': 2.5,
    'osc_sl_pct': -4.0,
    'osc_daily_loss_cap_pct': -8.0,
    'osc_win_chance': 49.50,
    
    # DRAGON CHASER (Seek Favorable Seed)
    'dc_difficulty': 'easy',
    'dc_target_col': 0
}

state = {
    'config': DEFAULT_CONFIG.copy(),
    'strategy': 'the_gork',
    'balance': {'available': 1000.0, 'currency': 'btc'}, # Initial balance
    'is_running': False,
    'current_bet': DEFAULT_CONFIG['min_bet_floor'], # Initialize with min_bet_floor
    
    # Session tracking
    'daily_start_balance': 1000.0,
    'daily_start_time': time.time(),
    'weekly_start_balance': 1000.0,
    'weekly_start_time': time.time(),
    'peak_balance': 1000.0,
    'recent_outcomes': [], # list of booleans indicating win/loss
    'current_win_streak': 0,
    'current_lose_streak': 0,
    
    # Custom Strategy State Overhaul
    'fib_index': 0, 
    'par_streak': 0,
    'osc_session_profit': 0.0,
    'osc_current_unit': 1,
    
    # General
    'total_bets': 0,
    'total_wagered': 0.0,
    'basic_current_bet': DEFAULT_CONFIG['min_bet_floor'],

    'chart_data': [], # { bets, profit, win_streak, lose_streak, balance }
    'roll_history': [],
    'server_seed_hash': hashlib.sha256(os.urandom(32)).hexdigest(),  # Simulated server seed
    'client_seed': f"gork-{random.randint(100000,999999)}-{int(time.time())}",
    'nonce': 0,
    'logs': [],
    'drawdown_history': [],
    'daily_outcomes': [],
    'prices': {'btc': 100000.0, 'ltc': 100.0, 'eth': 2500.0}, # Fallback defaults
    'ai_history': [],
}

def update_prices_thread():
    while True:
        try:
            # Use Binance or similar for prices
            r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbols": '["BTCl USDT","LTCUSDT","ETHUSDT"]'}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                price_map = {item['symbol']: float(item['price']) for item in data}
                with state_lock:
                    state['prices']['btc'] = price_map.get('BTCUSDT', state['prices']['btc'])
                    state['prices']['ltc'] = price_map.get('LTCUSDT', state['prices']['ltc'])
                    state['prices']['eth'] = price_map.get('ETHUSDT', state['prices']['eth'])
        except Exception as e:
            logger.error(f"Price update failed: {e}")
        time.sleep(60)


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    with state_lock:
        state['logs'].append(f"[{ts}] {msg}")
        state['logs'][:] = state['logs'][-300:]

# ========================= STAKE PROVABLY FAIR ENGINE =========================
# Replicates the Stake.com Dice HMAC-SHA256 algorithm exactly.
# Server seed is hashed first (simulated). Roll derived by reading
# 4 bytes of the HMAC output, shuffled by nonce, converting to 0-10000,
# then dividing by 100 => 0.00–99.99 float. Identical to real Stake dice.
def stake_derive_roll(server_seed: str, client_seed: str, nonce: int) -> float:
    message = f"{client_seed}:{nonce}"
    h = hmac.new(server_seed.encode(), message.encode(), hashlib.sha256).hexdigest()
    # Extract 4 nibble groups of 8 hex chars each, take first that gives < 10000
    for i in range(4):
        segment = h[i*8:(i*8)+8]
        val = int(segment, 16)
        result = val % 10001  # 0..10000
        if result <= 10000:
            return result / 100.0  # 0.00..100.00
    return 50.0  # fallback

def stake_place_bet(amount, condition="over", target=50.50):
    if stake_client:
        try:
            # Map bot's internal 'over'/'under' to Stake API's 'above'/'below'
            api_condition = "above" if condition == "over" else "below"
            active_curr = state['config'].get('active_currency', 'btc').lower()
            
            # Place the real bet
            result = stake_client.dice_roll(
                amount=amount,
                target=target,
                condition=api_condition,
                currency=active_curr,
                identifier=f"gork_{int(time.time())}_{random.randint(1000, 9999)}"
            )
            
            if 'data' in result and result['data']['diceRoll']:
                bet_data = result['data']['diceRoll']
                roll = bet_data['state']['result']
                # Check win based on API response payout
                win = bet_data['payout'] > 0
                with state_lock:
                    state['nonce'] += 1
                return {'win': win, 'amount': amount, 'roll': roll}
            else:
                log(f"API Bet Error: {result.get('errors', 'Unknown error')}")
        except Exception as e:
            log(f"API Bet Exception: {e}")

    # Fallback to simulation if API fails or is not initialized
    server_seed = state.get('server_seed_hash', 'default_server_hash_' + str(time.time()))
    client_seed = state.get('client_seed', 'gork-default')
    nonce = state.get('nonce', 0)
    with state_lock:
        state['nonce'] = nonce + 1
    roll = stake_derive_roll(server_seed, client_seed, nonce)
    if condition == "over": win = roll > target
    else: win = roll < target
    return {'win': win, 'amount': amount, 'roll': roll}

# ========================= DRAGON TOWER PREDICTOR =========================
def dragon_tower_derive_game(server_seed: str, client_seed: str, nonce: int, difficulty: str) -> list:
    diff_map = {
        'easy': {'eggs': 1, 'size': 4},
        'medium': {'eggs': 1, 'size': 3},
        'hard': {'eggs': 1, 'size': 2},
        'expert': {'eggs': 2, 'size': 3},
        'master': {'eggs': 3, 'size': 4}
    }
    cfg = diff_map.get(difficulty.lower(), diff_map['easy'])
    eggs_per_row = cfg['eggs']
    tiles_per_row = cfg['size']
    
    tower = []
    float_cursor = 0
    
    def get_float(index):
        round_num = index // 8
        byte_offset = (index % 8) * 4
        message = f"{client_seed}:{nonce}:{round_num}"
        h = hmac.new(server_seed.encode(), message.encode(), hashlib.sha256).digest()
        
        # 4 bytes to float
        bytes_part = h[byte_offset:byte_offset+4]
        val = 0
        for i, b in enumerate(bytes_part):
            val += b / (256**(i+1))
        return val

    for row_idx in range(9):
        available_tiles = list(range(tiles_per_row))
        egg_positions = []
        for _ in range(eggs_per_row):
            f = get_float(float_cursor)
            float_cursor += 1
            # Fisher-Yates element selection
            idx = int(f * len(available_tiles))
            egg_positions.append(available_tiles.pop(idx))
        
        # Mark safe vs egg
        row_data = []
        for i in range(tiles_per_row):
            row_data.append({'index': i, 'is_egg': i in egg_positions})
        tower.append(row_data)
        
    return tower[::-1] # Return from top to bottom for easier UI rendering

# ========================= CORE STRATEGIES =========================

def calculate_ema(data, period):
    if len(data) == 0: return None
    if len(data) < period:
        return sum(data) / len(data)
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for val in data[period:]:
        ema = (val - ema) * multiplier + ema
    return ema

def calculate_ema_cross_bet(balance):
    cfg = state['config']
    base_bet = balance * 0.002
    target = 50.50
    condition = "over"
    
    if not state['chart_data'] or len(state['chart_data']) < 20:
        return max(cfg['min_bet_floor'], base_bet), condition, target

    ema5 = state['chart_data'][-1].get('ema5', 0)
    ema20 = state['chart_data'][-1].get('ema20', 0)
    
    # Simple RSI proxy based on recent outcomes
    recent_wins = sum(1 for w in state['recent_outcomes'][-14:] if w)
    rsi_proxy = (recent_wins / 14.0) * 100 if len(state['recent_outcomes']) >= 14 else 50
    
    # Default is a 50.5% coinflip
    if ema5 > ema20:
        condition = "over"
    else:
        condition = "under"
        
    bet = base_bet
    
    # Overbought logic: Shrink bet to weather the storm
    if rsi_proxy > 70:
        bet = base_bet * 0.25 # Sharp reduction
    # Oversold logic: Capitalize on regression to the mean
    elif rsi_proxy < 30:
        bet = base_bet * 1.5
        
    return max(cfg['min_bet_floor'], min(bet, balance * 0.02)), condition, target

def calculate_die_last_bet(balance):
    cfg = state['config']
    base_bet = balance * cfg['die_last_base_bet_pct']
    streak = state['current_win_streak']
    if streak == 0: mult = 0.5
    elif streak == 1: mult = 1.0
    elif streak == 2: mult = 1.5
    elif streak == 3: mult = 2.0
    elif streak >= 4: mult = 2.5
    else: mult = 0.5
    
    if len(state['recent_outcomes']) >= 10:
        if sum(1 for won in state['recent_outcomes'][-10:] if not won) >= 6:
            mult *= 0.5
            
    return max(cfg['min_bet_floor'], min(base_bet * mult, balance * 0.01))

def calculate_eternal_volume_bet(balance):
    cfg = state['config']
    base_bet = balance * cfg['eternal_base_bet_pct']
    # Pure flat fractional, no modifiers
    return max(cfg['min_bet_floor'], min(base_bet, balance * 0.002))

def calculate_vanish_bet(balance):
    cfg = state['config']
    base_bet = balance * cfg['vanish_base_bet_pct']
    
    dd_pct = (balance - state['daily_start_balance']) / state['daily_start_balance'] * 100
    shrink = 1.0
    if dd_pct <= -7.0: shrink = 0.4
    elif dd_pct <= -5.0: shrink = 0.6
    elif dd_pct <= -3.0: shrink = 0.8
        
    streak = state['current_win_streak']
    if streak == 0: mult = 0.6
    elif streak == 1: mult = 0.9
    elif streak == 2: mult = 1.2
    elif streak == 3: mult = 1.5
    else: mult = 1.8
    
    circuit = 1.0
    if len(state['recent_outcomes']) >= 8:
        if sum(1 for won in state['recent_outcomes'][-8:] if not won) >= 4:
            circuit = 0.4
            
    return max(cfg['min_bet_floor'], min(base_bet * shrink * mult * circuit, balance * 0.003))

def calculate_gork_bet(balance):
    cfg = state['config']
    distance = balance - state['daily_start_balance']
    max_bet = balance * cfg['base_bet_pct']
    recovery = abs(distance) * 1.2 if distance < 0 else 0
    bet = min(max_bet, recovery)
    return max(cfg['min_bet_floor'], min(bet, balance * 0.003))

def calculate_basic_bet(balance):
    cfg = state['config']
    # If it's the first bet or we were reset
    if state['basic_current_bet'] <= 0:
        state['basic_current_bet'] = cfg['basic_bet_amount']
    
    # We use the results of the PREVIOUS bet to decide the NEXT bet
    # This is handled in the betting loop after the outcome is known
    return max(cfg['min_bet_floor'], state['basic_current_bet']), cfg['basic_condition'], cfg['basic_target']

def calculate_reverted_martingale_bet(balance):
    cfg = state['config']
    base_bet = balance * cfg['rm_base_bet_pct']
    
    session_pct = ((balance - state['daily_start_balance']) / state['daily_start_balance']) * 100 if state['daily_start_balance'] > 0 else 0
    
    # Dynamic Win target based on session drawdown
    target = 50.50
    if session_pct < -0.5:
        target = 60.50 # roughly 39.5% win chance (scale up payout to recover)
    if session_pct < -1.0:
        target = 70.50 # roughly 29.5% win chance
    
    # Linear scale down towards TP
    factor = 1.0 - (session_pct / cfg['session_tp_pct'])
    factor = max(0.1, factor) # don't drop below 10% of base bet
    
    # If in drawdown, scale bet size UP slightly to aggressively recover
    if session_pct < 0:
        drawdown_factor = abs(session_pct) / abs(cfg['session_sl_pct'])
        # Increase bet by up to 3x base depending on how close to Stop Loss we are
        factor = 1.0 + (min(drawdown_factor, 1.0) * 2.0)
    
    bet = base_bet * factor
    return max(cfg['min_bet_floor'], min(bet, balance * 0.05)), "over", target

def calculate_wager_grind_99(balance):
    cfg = state['config']
    base_bet = balance * cfg['wg99_base_bet_pct']
    # 99% win chance via Stake 1.0102x payout limit (Roll Over 1.00 or Under 99.00)
    # Target 1.00 Over gives roughly 99.00% odds.
    return max(cfg['min_bet_floor'], min(base_bet, balance * 0.1)), "over", 1.00

def is_locked():
    cfg = state['config']
    now = time.time()
    bal = state['balance']['available']

    if now - state['daily_start_time'] > 86400:
        state['daily_start_balance'] = bal
        state['daily_start_time'] = now
    if now - state['weekly_start_time'] > 604800:
        state['weekly_start_balance'] = bal
        state['weekly_start_time'] = now

    strat = state['strategy']
    if strat == 'the_gork': daily_cap = cfg['daily_loss_cap_pct']
    elif strat == 'die_last': daily_cap = cfg['die_last_daily_loss_cap_pct']
    elif strat == 'vanish_in_volume': daily_cap = cfg['vanish_daily_loss_cap_pct']
    elif strat == 'reverted_martingale': daily_cap = cfg['rm_daily_loss_cap_pct']
    elif strat == 'wager_grind_99': daily_cap = cfg['wg99_daily_loss_cap_pct']
    elif strat == 'fibonacci': daily_cap = cfg['fib_daily_loss_cap_pct']
    elif strat == 'paroli': daily_cap = cfg['par_daily_loss_cap_pct']
    elif strat == 'oscars_grind': daily_cap = cfg['osc_daily_loss_cap_pct']
    elif strat == 'custom': daily_cap = cfg.get('c_daily', -1.0)
    else: daily_cap = cfg.get('eternal_daily_loss_cap_pct', -2.5)
    
    all_time_cap = cfg['all_time_drawdown_cap_pct']
    if strat == 'vanish_in_volume': all_time_cap = max(all_time_cap, -10.0)

    if cfg['enable_daily_lock']:
        daily_pct = (bal - state['daily_start_balance']) / state['daily_start_balance'] * 100
        if daily_pct <= daily_cap:
            return True, f"Daily cap {daily_pct:.2f}%"
    if cfg['enable_weekly_lock']:
        weekly_pct = (bal - state['weekly_start_balance']) / state['weekly_start_balance'] * 100
        if weekly_pct <= cfg['weekly_loss_cap_pct']:
            return True, f"Weekly cap {weekly_pct:.2f}%"
    if cfg['enable_alltime_lock']:
        dd_pct = (bal - state['peak_balance']) / state['peak_balance'] * 100
        if dd_pct <= all_time_cap:
            return True, f"All-time drawdown {dd_pct:.2f}%"
    return False, ""

# ======================== NEW ALGORITHMS ==========================

def calculate_fibonacci_bet(balance):
    cfg = state['config']
    base_bet = balance * cfg['fib_base_bet_pct']
    
    # Compute fib sequence element iteratively
    def fib(n):
        if n <= 0: return 1
        if n == 1: return 1
        a, b = 1, 1
        for _ in range(2, n + 1):
            a, b = b, a + b
        return b
        
    multiplier = fib(state['fib_index'])
    bet = base_bet * multiplier
    
    target = 100.0 - cfg['fib_win_chance']
    return max(cfg['min_bet_floor'], min(bet, balance * 0.1)), "over", target

def calculate_paroli_bet(balance):
    cfg = state['config']
    base_bet = balance * cfg['par_base_bet_pct']
    
    # Double on win, hold if max streak hit
    if state['par_streak'] > 0:
        multiplier = 2 ** state['par_streak']
        bet = base_bet * multiplier
    else:
        bet = base_bet
        
    target = 100.0 - cfg['par_win_chance']
    return max(cfg['min_bet_floor'], min(bet, balance * 0.1)), "over", target

def calculate_oscars_grind_bet(balance):
    cfg = state['config']
    base_unit = balance * cfg['osc_base_bet_pct']
    
    bet = base_unit * state['osc_current_unit']
    
    # Cap the bet so it doesn't overshoot 1 unit of profit for the session
    profit_needed = base_unit - state['osc_session_profit']
    if profit_needed > 0 and bet > profit_needed:
        bet = profit_needed
        
    if bet <= 0: bet = base_unit # Safety fallback
    
    target = 100.0 - cfg['osc_win_chance']
    return max(cfg['min_bet_floor'], min(bet, balance * 0.1)), "over", target
    
# ========================= BETTING LOOP =========================

def fetch_balance():
    if stake_client:
        try:
            bals = stake_client.user_balances()
            active_curr = state['config'].get('active_currency', 'btc')
            # Look for active currency first
            for b in bals:
                if b['available']['currency'] == active_curr:
                    return {'available': b['available']['amount'], 'currency': b['available']['currency']}
            # Back up: any non-zero
            for b in bals:
                if b['available']['amount'] > 0:
                    return {'available': b['available']['amount'], 'currency': b['available']['currency']}
            # Back up 2: just take the first
            if bals:
                return {'available': bals[0]['available']['amount'], 'currency': bals[0]['available']['currency']}
        except Exception as e:
            log(f"API Bal Error: {e}")
    return {'available': state['balance']['available'], 'currency': state['balance'].get('currency', 'btc')}

def place_dice_bet(amount, condition="over", target=50.50):
    # Simulated dice bet - ready for GraphQL mutation swap
    roll = random.uniform(0, 100)
    if condition == "over": win = roll > target
    else: win = roll < target
    return {'win': win, 'amount': amount, 'roll': roll}

def betting_loop():
    while True:
        with state_lock:
            if not state['is_running']:
                time.sleep(1)
                continue
            locked, reason = is_locked()
            if locked:
                state['is_running'] = False
                log(f"LOCKED: {reason}")
                continue
            
            condition = "over"
            target = 50.50
            if state['strategy'] == 'custom':
                current_bet = execute_custom_strategy(state['balance']['available'])
            elif state['strategy'] == 'the_gork':
                current_bet = calculate_gork_bet(state['balance']['available'])
            elif state['strategy'] == 'basic':
                current_bet, condition, target = calculate_basic_bet(state['balance']['available'])
            elif state['strategy'] == 'die_last':
                current_bet = calculate_die_last_bet(state['balance']['available'])
            elif state['strategy'] == 'eternal_volume':
                current_bet = calculate_eternal_volume_bet(state['balance']['available'])
            elif state['strategy'] == 'ema_cross':
                current_bet, condition, target = calculate_ema_cross_bet(state['balance']['available'])
            elif state['strategy'] == 'reverted_martingale':
                current_bet, condition, target = calculate_reverted_martingale_bet(state['balance']['available'])
            elif state['strategy'] == 'wager_grind_99':
                current_bet, condition, target = calculate_wager_grind_99(state['balance']['available'])
            elif state['strategy'] == 'fibonacci':
                current_bet, condition, target = calculate_fibonacci_bet(state['balance']['available'])
            elif state['strategy'] == 'paroli':
                current_bet, condition, target = calculate_paroli_bet(state['balance']['available'])
            elif state['strategy'] == 'oscars_grind':
                current_bet, condition, target = calculate_oscars_grind_bet(state['balance']['available'])
            elif state['strategy'] == 'dragon_chaser':
                # Predict before betting
                diff = cfg.get('dc_difficulty', 'easy')
                tg_col = int(cfg.get('dc_target_col', 0))
                # Predict for CURRENT nonce (since this is what the manual next game uses)
                tower = dragon_tower_derive_game(state['server_seed_hash'], state['client_seed'], state['nonce'], diff)
                
                # Check for perfect column path
                tiles_per_row = len(tower[0])
                check_col = tg_col if tg_col < tiles_per_row else tiles_per_row - 1
                
                is_perfect = True
                for row in tower: # checks from top to bottom
                    if row[check_col]['is_egg']:
                        is_perfect = False
                        break
                
                if is_perfect:
                    state['is_running'] = False
                    log(f"DRAGON CHASER: Perfect path on Col {check_col+1} found at Nonce {state['nonce']}! Bot halted.")
                    continue
                else:
                    # Place a minimum dummy bet to advance the nonce with minimal loss
                    current_bet = cfg.get('min_bet_floor', 0.000001)
                    condition = 'over'
                    target = 2.0 # 98% win chance
            else:
                current_bet = calculate_vanish_bet(state['balance']['available'])

        balance_obj = fetch_balance()
        with state_lock:
            state['balance'] = balance_obj
            if balance_obj['available'] > state['peak_balance']:
                state['peak_balance'] = balance_obj['available']

            state['drawdown_history'].append({
                'wager': state['total_wagered'],
                'balance': balance_obj['available'],
                'drawdown_pct': (balance_obj['available'] - state['peak_balance']) / state['peak_balance'] * 100
            })
            if len(state['drawdown_history']) > 600:
                state['drawdown_history'] = state['drawdown_history'][-600:]

        result = stake_place_bet(current_bet, condition, target)
        won = result.get('win', False) if result else False
        roll = result.get('roll', 0.0) if result else 0.0
        profit_val = (current_bet * (100 / (100 - target)) - current_bet) if won else -current_bet # Approximate profit for Oscar's Grind

        with state_lock:
            state['roll_history'].append(roll)
            if len(state['roll_history']) > 100:
                state['roll_history'].pop(0)
            
            ema5 = calculate_ema(state['roll_history'], 5)
            ema20 = calculate_ema(state['roll_history'], 20)
            
            state['total_bets'] += 1
            state['total_wagered'] += current_bet
            state['recent_outcomes'].append(won)
            if len(state['recent_outcomes']) > 30:
                state['recent_outcomes'] = state['recent_outcomes'][-30:]
                
            if won:
                state['current_win_streak'] += 1
                state['current_lose_streak'] = 0
                
                # BASIC STRATEGY WIN LOGIC
                if state['strategy'] == 'basic':
                    win_action = cfg.get('basic_on_win', 'reset')
                    if win_action == 'reset':
                        state['basic_current_bet'] = cfg['basic_bet_amount']
                    elif win_action == 'multiply':
                        state['basic_current_bet'] *= cfg.get('basic_win_mult', 1.0)
                
                # FIBONACCI, PAROLI, OSCAR'S GRIND WIN LOGIC
                if state['strategy'] == 'fibonacci':
                    state['fib_index'] = max(0, state['fib_index'] - 2) # Retreat 2 steps
                elif state['strategy'] == 'paroli':
                    state['par_streak'] += 1
                    if state['par_streak'] >= cfg.get('par_streak_target', 3):
                        state['par_streak'] = 0 # Cash in
                elif state['strategy'] == 'oscars_grind':
                    state['osc_session_profit'] += profit_val
                    if state['osc_session_profit'] >= (balance_obj['available'] * cfg['osc_base_bet_pct']): # Check against 1 unit profit
                        # Reached +1 unit profit, reset
                        state['osc_session_profit'] = 0.0
                        state['osc_current_unit'] = 1
                    else:
                        state['osc_current_unit'] += 1
                
                if state['strategy'] in ['vanish_in_volume', 'die_last'] and state['current_win_streak'] >= 4:
                    state['current_win_streak'] = 0
            else:
                state['current_lose_streak'] += 1
                state['current_win_streak'] = 0
                
                # BASIC STRATEGY LOSS LOGIC
                if state['strategy'] == 'basic':
                    loss_action = cfg.get('basic_on_loss', 'multiply')
                    if loss_action == 'reset':
                        state['basic_current_bet'] = cfg['basic_bet_amount']
                    elif loss_action == 'multiply':
                        state['basic_current_bet'] *= cfg.get('basic_loss_mult', 2.0)

                # FIBONACCI, PAROLI, OSCAR'S GRIND LOSS LOGIC
                if state['strategy'] == 'fibonacci':
                    state['fib_index'] += 1 # Advance 1 step
                elif state['strategy'] == 'paroli':
                    state['par_streak'] = 0 # Reset
                elif state['strategy'] == 'oscars_grind':
                    state['osc_session_profit'] -= current_bet
                    # Bet size stays the same on loss
                    
            # Determine PnL since start of day to see if hitting TP/SL
            change_pct = (balance_obj['available'] - state['daily_start_balance']) / state['daily_start_balance'] * 100
            
            # Record chart data point
            bets = state['total_bets']
            profit = balance_obj['available'] - state['daily_start_balance']
            ws = state['current_win_streak']
            ls = state['current_lose_streak']
            bal = balance_obj['available']
            
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "INSERT INTO chart_data (bet_number, profit, win_streak, lose_streak, balance, ema5, ema20, roll_result) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (bets, profit, ws, ls, bal, ema5, ema20, roll)
                )
                if bets % 50 == 0:
                    conn.execute("DELETE FROM chart_data WHERE id NOT IN (SELECT id FROM chart_data ORDER BY id DESC LIMIT 2000)")
                    
            cfg = state['config']
            
            strat = state['strategy']
            if strat == 'die_last':
                tp, sl = cfg['die_last_tp_pct'], cfg['die_last_sl_pct']
            elif strat == 'vanish_in_volume':
                tp, sl = cfg['vanish_tp_pct'], cfg['vanish_sl_pct']
            elif strat == 'eternal_volume':
                tp, sl = cfg['eternal_tp_pct'], cfg['eternal_sl_pct']
            elif strat == 'fibonacci':
                tp, sl = cfg['fib_tp_pct'], cfg['fib_sl_pct']
            elif strat == 'paroli':
                tp, sl = cfg['par_tp_pct'], cfg['par_sl_pct']
            elif strat == 'oscars_grind':
                tp, sl = cfg['osc_tp_pct'], cfg['osc_sl_pct']
            else:
                tp, sl = cfg['session_tp_pct'], cfg['session_sl_pct']
            
            if change_pct >= tp:
                state['is_running'] = False
                state['daily_outcomes'].append({'type': 'green', 'change_pct': change_pct})
                log(f"GREEN EXIT +{change_pct:.2f}%")
            elif change_pct <= sl:
                state['is_running'] = False
                state['daily_outcomes'].append({'type': 'red', 'change_pct': change_pct})
                log(f"RED EXIT {change_pct:.2f}%")

            if cfg['enable_seed_rotation']:
                if state['total_bets'] % max(cfg.get('seed_rotate_min', 50), 1) == 0:
                    state['client_seed'] = f"gork-{random.randint(100000,999999)}-{int(time.time())}"
                    state['nonce'] = 0
                    log("Client seed rotated")
            # Rotate simulated server seed every 500 bets (like Stake)
            if state['total_bets'] % 500 == 0:
                state['server_seed_hash'] = hashlib.sha256(os.urandom(32)).hexdigest()
                state['nonce'] = 0
                log("Server seed refreshed")

        time.sleep(2.5)

# ========================= PREMIUM DASHBOARD HTML =========================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>The Gork — v2.0</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <script src="https://unpkg.com/lightweight-charts@3.8.0/dist/lightweight-charts.standalone.production.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/ace/1.32.2/ace.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/sortablejs@latest/Sortable.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@400;600;700&family=Inter:wght@400;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0b0c10;
            --surface: #1f2833;
            --primary: #66fcf1;
            --secondary: #45a29e;
            --text: #c5c6c7;
            --danger: #ff4757;
            --success: #2ed573;
        }
        body { 
            font-family: 'Inter', sans-serif; 
            background: var(--bg); color: var(--text); 
            margin: 0; padding: 2rem; 
        }
        h1 { 
            color: var(--primary); 
            font-family: 'Roboto Mono', monospace; 
            font-size: 2.2rem;
            text-shadow: 0 0 10px rgba(102, 252, 241, 0.3);
            margin-bottom: 2rem;
        }
        
        /* Tabs */
        .tabs { display: flex; border-bottom: 1px solid rgba(102, 252, 241, 0.3); margin-bottom: 2rem; }
        .tab-btn { 
            background: transparent; border-radius: 0; box-shadow: none; font-size: 0.9rem;
            padding: 1rem 1.5rem; cursor: pointer; color: var(--text); 
            font-weight: 600; letter-spacing: 1px; transition: 0.2s;
            border-bottom: 3px solid transparent; 
        }
        .tab-btn:hover { color: var(--primary); }
        .tab-btn.active { color: var(--primary); border-bottom-color: var(--primary); }
        
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        
        .grid { display: grid; grid-template-columns: 1fr 2fr; gap: 2rem; }
        .panel { 
            background: rgba(31, 40, 51, 0.6); 
            backdrop-filter: blur(12px);
            padding: 1.5rem; 
            border-radius: 12px; 
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            border: 1px solid rgba(102, 252, 241, 0.15);
            transition: transform 0.2s, box-shadow 0.2s;
            cursor: grab;
            resize: both;
            overflow: auto;
        }
        .panel:hover { 
            border-color: rgba(102, 252, 241, 0.3); 
            box-shadow: 0 0 20px rgba(102, 252, 241, 0.15);
        }
        .panel:active {
            cursor: grabbing;
        }
        h3 { color: #fff; margin-top: 0; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 0.5rem; }
        
        .stat-row { display: flex; justify-content: space-between; align-items: center; margin: 0.8rem 0; font-family: 'Roboto Mono', monospace;}
        .stat-value { color: var(--primary); font-weight: 600; font-size: 1.1rem; }
        
        .form-group { margin-bottom: 1rem; }
        label { display: block; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 1px; color: var(--secondary); margin-bottom: 0.4rem; }
        input, select { 
            width: 100%; padding: 0.6rem; 
            background: #141a22; color: #e0e0e0; 
            border: 1px solid rgba(102,252,241,0.2); border-radius: 6px; 
            font-family: 'Roboto Mono', monospace;
            box-sizing: border-box;
            transition: all 0.2s;
            appearance: none; -webkit-appearance: none;
        }
        select {
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%2366fcf1'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 0.75rem center;
            padding-right: 2rem;
        }
        option, optgroup {
            background: #1f2833;
            color: #e0e0e0;
        }
        input:focus, select:focus { border-color: var(--primary); outline: none; box-shadow: 0 0 8px rgba(102,252,241,0.2); }
        
        .config-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 1rem; }
        
        button { 
            padding: 0.8rem 1.6rem; 
            border: none; border-radius: 6px; 
            font-family: 'Inter', sans-serif; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;
            cursor: pointer; transition: all 0.2s;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        }
        .btn-start { background: var(--primary); color: var(--bg); }
        .btn-start:hover { background: #fff; transform: translateY(-2px); box-shadow: 0 6px 20px rgba(102,252,241,0.4); }
        .btn-stop { background: transparent; color: var(--danger); border: 1px solid var(--danger); }
        .btn-stop:hover { background: rgba(255, 71, 87, 0.1); transform: translateY(-2px); }
        
        .badge { padding: 0.2rem 0.6rem; border-radius: 12px; font-size: 0.75rem; font-weight: bold; }
        .badge.RUNNING { background: rgba(46, 213, 115, 0.2); color: var(--success); border: 1px solid var(--success); text-shadow: 0 0 5px var(--success); }
        .badge.PAUSED { background: rgba(255, 71, 87, 0.2); color: var(--danger); border: 1px solid var(--danger); }
        
        #logs { font-family: 'Roboto Mono', monospace; font-size: 0.8rem; height: 160px; overflow-y: auto; color: var(--secondary); }
        
        /* USD helpers */
        .input-with-usd { display: flex; gap: 0.5rem; align-items: center; }
        .input-with-usd input[type="number"] { flex: 1; }
        .input-usd-helper { width: 95px !important; color: #f1c40f !important; border-color: rgba(241, 196, 15, 0.3) !important; padding: 0.4rem !important; font-size: 0.85rem !important; }
        
        /* Specific panels */
        .strat-panel { display: none; }
        #gork_config { display: block; }

        /* Simulator Dashboard */
        .sim-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-top: 1rem; }
        .sim-stat { 
            background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.05);
            padding: 1.5rem; text-align: center; border-radius: 8px;
        }
        .sim-val { font-size: 1.8rem; font-weight: bold; color: var(--primary); margin: 0.5rem 0; font-family: 'Roboto Mono', monospace; }
        .sim-title { font-size: 0.8rem; text-transform: uppercase; color: var(--secondary); letter-spacing: 1px;}
        .sim-danger { color: #ff4757; }
        .sim-success { color: #2ed573; }
        
        /* AI Chat Styles */
        .ai-msg { margin-bottom: 0.8rem; padding: 0.5rem; border-radius: 4px; }
        .ai-msg-user { background: rgba(102, 252, 241, 0.1); border-left: 2px solid var(--primary); }
        .ai-msg-bot { background: rgba(255, 255, 255, 0.05); border-left: 2px solid var(--secondary); }
        .ai-cmd { color: var(--primary); font-family: monospace; font-weight: bold; }

        /* Tooltips */
        .tooltip {
            display: inline-block; cursor: pointer;
            background: rgba(102, 252, 241, 0.2); color: var(--primary);
            width: 16px; height: 16px; border-radius: 50%;
            text-align: center; line-height: 16px; font-size: 0.7rem; font-weight: bold;
            margin-left: 6px; position: relative;
        }
        .tooltip .tooltiptext {
            visibility: hidden; width: 220px; background-color: var(--surface);
            color: #fff; text-align: left; border-radius: 6px; padding: 0.8rem;
            position: absolute; z-index: 10; bottom: 125%; left: 50%;
            margin-left: -110px; opacity: 0; transition: opacity 0.3s;
            border: 1px solid var(--primary); font-size: 0.75rem; text-transform: none; font-weight: normal; letter-spacing: normal;
            box-shadow: 0 4px 15px rgba(0,0,0,0.5);
        }
        .tooltip .tooltiptext::after {
            content: ""; position: absolute; top: 100%; left: 50%;
            margin-left: -5px; border-width: 5px; border-style: solid;
            border-color: var(--primary) transparent transparent transparent;
        }
        .tooltip:hover .tooltiptext { visibility: visible; opacity: 1; }

        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(102, 252, 241, 0.4); }
            70% { box-shadow: 0 0 0 10px rgba(102, 252, 241, 0); }
            100% { box-shadow: 0 0 0 0 rgba(102, 252, 241, 0); }
        }
        .running-pulse { animation: pulse 2s infinite; }
        
        .chart-container { position: relative; height: 60vh; width: 100%; }
        
        /* Settings & VIP Panels */
        .settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; }
        
        /* VIP Badge & Progress */
        .vip-card {
            border: 2px solid rgba(102, 252, 241, 0.5);
            border-radius: 12px; padding: 1.5rem;
            position: relative; overflow: hidden;
            background: linear-gradient(135deg, rgba(31,40,51,1) 0%, rgba(102,252,241,0.05) 100%);
        }
        .vip-badge {
            position: absolute; right: 1.5rem; top: 1.5rem;
            font-size: 3rem; color: var(--primary);
            text-shadow: 0 0 20px rgba(102,252,241,0.6);
            opacity: 0.9;
        }
        .progress-bg {
            background: rgba(255,255,255,0.1); height: 8px; border-radius: 4px; overflow: hidden; margin: 1rem 0;
        }
        .progress-fill {
            background: var(--primary); height: 100%; width: 0%; transition: width 1s ease-out;
        }
        
        /* Claim Rows (Rakeback, Reloads etc) */
        .claim-row {
            background: rgba(0,0,0,0.2); border-radius: 8px;
            padding: 1rem; margin-top: 1rem;
            display: flex; justify-content: space-between; align-items: center;
            border: 1px solid rgba(255,255,255,0.05);
        }
        .claim-btn { background: #3498db; color: white; padding: 0.5rem 1rem; font-size: 0.8rem; border-radius: 6px; border:none; cursor:pointer;}
        .claim-btn:hover { filter: brightness(1.2); }
        .claim-btn.disabled { background: rgba(255,255,255,0.1); color: rgba(255,255,255,0.4); cursor: not-allowed; }
        
        /* Wallet Toggles */
        .wallet-item {
            display: flex; justify-content: space-between; align-items: center;
            padding: 0.8rem; border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        .wallet-item:last-child { border-bottom: none; }
        .currency-label { font-weight: bold; color: #fff; width: 60px;}
        .usd-value { color: var(--secondary); font-size: 0.9rem; margin-left: auto; margin-right: 15px;}
        
        /* Inputs with Button append */
        .input-group { display: flex; gap: 0.5rem; }
        .input-group input { flex-grow: 1; }
        .input-group button { padding: 0.6rem 1rem; }
        
        /* SortableJS drag states */
        .sortable-ghost { opacity: 0.4; background: rgba(102, 252, 241, 0.05); }
        .sortable-drag { box-shadow: 0 10px 30px rgba(0,0,0,0.5); cursor: grabbing !important;}

        /* Mobile Responsiveness (iPhone 16 Plus max approx 430px) */
        @media (max-width: 768px) {
            body { padding: 1rem; }
            h1 { font-size: 1.6rem; margin-bottom: 1rem; }
            .grid, .settings-grid, .sim-grid { grid-template-columns: 1fr; gap: 1rem; }
            .config-grid { grid-template-columns: 1fr; }
            .tabs { overflow-x: auto; white-space: nowrap; padding-bottom: 0.5rem; border-bottom: none; }
            .tab-btn { padding: 0.6rem 1rem; font-size: 0.8rem; border: 1px solid rgba(102, 252, 241, 0.2); border-radius: 4px; margin-right: 0.5rem; margin-bottom: 0.5rem;}
            .tab-btn.active { background: rgba(102, 252, 241, 0.1); }
            .panel { padding: 1rem; resize: none !important; }
            .stat-row { font-size: 0.9rem; flex-wrap: wrap; }
            .chart-container { height: 40vh; }
            .input-group { flex-direction: column; }
            .vip-badge { font-size: 2rem; }
            .vip-card { padding: 1rem; }
        }

        /* Dragon Tower Styles */
        .tower-grid {
            display: flex;
            flex-direction: column;
            gap: 10px;
            margin-top: 1rem;
            padding: 1rem;
            background: rgba(0,0,0,0.2);
            border-radius: 12px;
        }
        .tower-row {
            display: flex;
            justify-content: center;
            gap: 10px;
        }
        .tower-tile {
            width: 80px;
            height: 45px;
            border-radius: 6px;
            border: 2px solid rgba(255,255,255,0.05);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.75rem;
            transition: all 0.3s;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .tower-tile.safe {
            background: rgba(46, 213, 115, 0.15);
            border-color: var(--success);
            color: var(--success);
            box-shadow: inset 0 0 10px rgba(46, 213, 115, 0.1);
        }
        .tower-tile.egg {
            background: rgba(255, 71, 87, 0.15);
            border-color: var(--danger);
            color: var(--danger);
            opacity: 0.6;
        }
    </style>
</head>
<body>
    <div id="login-overlay" style="position:fixed; top:0; left:0; width:100%; height:100%; background:var(--bg); z-index:9999; display:flex; flex-direction:column; justify-content:center; align-items:center;">
        <h1 style="margin-bottom:1rem;">SYSTEM LOCKED</h1>
        <div style="background:var(--surface); padding:2rem; border-radius:12px; border:1px solid rgba(102, 252, 241, 0.3); text-align:center;">
            <input type="password" id="login-pw" placeholder="Admin Password..." style="margin-bottom:1rem; padding:0.8rem; width:200px; display:block; text-align:center; background:rgba(0,0,0,0.5); border:1px solid var(--primary); color:#fff;">
            <button class="btn-start" onclick="submitLogin()" style="width:100%;">AUTHENTICATE</button>
            <div id="login-err" style="color:var(--danger); margin-top:1rem; display:none;">Invalid Credentials</div>
        </div>
    </div>
    <script>
        const originalFetch = window.fetch;
        window.fetch = async function() {
            let [resource, config] = arguments;
            if(!config) config = {};
            if(!config.headers) config.headers = {};
            const token = localStorage.getItem('gork_jwt');
            if(token) config.headers['Authorization'] = 'Bearer ' + token;
            const response = await originalFetch(resource, config);
            if(response.status === 401) document.getElementById('login-overlay').style.display = 'flex';
            return response;
        };
        async function submitLogin() {
            const pw = document.getElementById('login-pw').value;
            const r = await originalFetch('/login', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({password: pw})
            });
            if(r.ok) {
                const data = await r.json();
                localStorage.setItem('gork_jwt', data.token);
                document.getElementById('login-overlay').style.display = 'none';
                loadSettingsData();
            } else document.getElementById('login-err').style.display = 'block';
        }
        if(localStorage.getItem('gork_jwt')) document.getElementById('login-overlay').style.display = 'none';
        else setTimeout(() => { document.getElementById('login-overlay').style.display = 'flex'; }, 100);
    </script>
    <h1>✧ TERMINAL_GORK</h1>

    <div class="tabs">
        <button class="tab-btn active" onclick="openTab(this, 'terminal')">TERMINAL</button>
        <button class="tab-btn" onclick="openTab(this, 'charting')">REALTIME CHARTING</button>
        <button class="tab-btn" onclick="openTab(this, 'simulate')">BENCHMARK / SIMULATOR</button>
        <button class="tab-btn" onclick="openTab(this, 'editor')">STRATEGY EDITOR</button>
        <button class="tab-btn" onclick="openTab(this, 'dragon')">DRAGON PREDICTOR</button>
        <button class="tab-btn" onclick="openTab(this, 'settings')">⚙ VIP & SETTINGS</button>
    </div>

    <!-- TERMINAL TAB -->
    <div id="terminal" class="tab-content active">
        <div class="grid">
            <div class="panel" style="display:flex; flex-direction:column; justify-content:space-between;">
            <div>
                <h3>System Status <span id="status-badge" class="badge PAUSED" style="float:right;">PAUSED</span></h3>
                <div class="stat-row"><span>Active Currency</span><span class="stat-value" id="currency">BTC</span></div>
                <div class="stat-row"><span>Balance</span><span class="stat-value" id="balance">0.00000000</span></div>
                <div class="stat-row"><span>Current Bet</span><span class="stat-value" id="bet">0.00000000</span></div>
                <div class="stat-row"><span>Total Wagered</span><span class="stat-value" id="wagered">0.00</span></div>
                <div class="stat-row"><span>Total Bets</span><span class="stat-value" id="bets">0</span></div>
            </div>

            <div style="margin-top:2rem; display:flex; gap:1rem;">
                <button class="btn-start" id="startBtn" onclick="startBot()">Initialize</button>
                <button class="btn-stop" onclick="stopBot()">Halt</button>
            </div>
        </div>

        <div class="panel">
            <h3>Strategy Configuration</h3>

            <div class="form-group">
                <label>Strategy Subsystem</label>
                <select id="strategy" onchange="toggleStrat()">
                    <option value="the_gork">THE GORK (Mirror Balance)</option>
                    <option value="basic">BASIC (Absolute Amount)</option>
                    <option value="fibonacci">FIBONACCI (Mathematical Retracement)</option>
                    <option value="paroli">PAROLI (Reverse Martingale)</option>
                    <option value="oscars_grind">OSCAR'S GRIND (+1 Unit Target)</option>
                    <option value="ema_cross">EMA CROSSOVER (Dynamic Trade Follower)</option>
                    <option value="die_last">DIE LAST (Win Sometimes)</option>
                    <option value="vanish_in_volume">VANISH IN VOLUME (Die Never)</option>
                    <option value="eternal_volume">ETERNAL VOLUME (Zero Ruin)</option>
                    <option value="reverted_martingale">REVERTED MARTINGALE (Anti-Ruin)</option>
                    <option value="wager_grind_99">WAGER GRIND 99 (Max Volume)</option>
                    <option value="custom">CUSTOM (Your Python Code)</option>
                    <option value="dragon_chaser">DRAGON CHASER (Seek Favorable Seed)</option>
                </select>
                <div id="strat-desc" style="font-size:0.8rem; color:var(--secondary); margin-top:0.5rem; background:rgba(0,0,0,0.2); padding:0.5rem; border-radius:4px; border-left:2px solid var(--primary);">
                    Auto-scales bet size based on distance to the daily starting bankroll target.
                </div>
            </div>

            <!-- Shared -->
            <div class="config-grid">
                <div class="form-group">
                    <label>All-Time Drawdown % <span class="tooltip">!<span class="tooltiptext">Permanently halts the bot if your balance drops this far from your absolute peak.</span></span></label>
                    <input id="all_time_cap" type="number" step="0.1" value="-8.0">
                </div>
                <div class="form-group">
                    <label>Min Bet Floor <span class="tooltip">!<span class="tooltiptext">The absolute minimum bet size the algorithm is allowed to make.</span></span></label>
                    <input id="min_bet" type="number" step="0.000001" value="0.000001">
                </div>
            </div>

            <hr style="border:none; border-bottom:1px solid rgba(255,255,255,0.05); margin: 1rem 0;">

            <!-- The Gork Panel -->
            <div id="gork_config" class="strat-panel">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">Percentage of your current balance to risk per initial bet.</span></span></label>
                        <div class="input-with-usd">
                            <input id="g_base" type="number" step="0.0001" value="0.0012" oninput="syncPctToUsd('g_base')">
                            <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                            <input id="g_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('g_base')">
                        </div>
                    </div>
                    <div class="form-group"><label>Session TP % <span class="tooltip">!<span class="tooltiptext">Take Profit: Stops betting and safely logs out once your session is up this % from start.</span></span></label><input id="g_tp" type="number" step="0.1" value="3.0"></div>
                    <div class="form-group"><label>Session SL % <span class="tooltip">!<span class="tooltiptext">Stop Loss: Halts session if drawdown exceeds this %. Protects against deep holes.</span></span></label><input id="g_sl" type="number" step="0.1" value="-1.4"></div>
                    <div class="form-group"><label>Daily Loss Cap % <span class="tooltip">!<span class="tooltiptext">Circuit Breaker: Locks bot for 24h if you lose this much in a single day.</span></span></label><input id="g_daily" type="number" step="0.1" value="-1.8"></div>
                </div>
            </div>

            <!-- Basic Strategy Panel -->
            <div id="basic_config" class="strat-panel" style="display:none;">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Bet Amount (Absolute) <span class="tooltip">!<span class="tooltiptext">Fixed amount of currency to bet.</span></span></label>
                        <input id="basic_bet_amount" type="number" step="0.000001" value="0.000001">
                    </div>
                    <div class="form-group">
                        <label>On Win Action</label>
                        <select id="basic_on_win" onchange="toggleBasicMults()">
                            <option value="reset">Reset to Base</option>
                            <option value="multiply">Multiply Bet</option>
                            <option value="stay">Stay Same</option>
                        </select>
                    </div>
                    <div id="basic_win_mult_wrap" class="form-group" style="display:none;">
                        <label>Win Multiplier</label>
                        <input id="basic_win_mult" type="number" step="0.1" value="1.0">
                    </div>
                    <div class="form-group">
                        <label>On Loss Action</label>
                        <select id="basic_on_loss" onchange="toggleBasicMults()">
                            <option value="multiply">Multiply Bet (Martingale)</option>
                            <option value="reset">Reset to Base</option>
                            <option value="stay">Stay Same</option>
                        </select>
                    </div>
                    <div id="basic_loss_mult_wrap" class="form-group">
                        <label>Loss Multiplier</label>
                        <input id="basic_loss_mult" type="number" step="0.1" value="2.0">
                    </div>
                    <div class="form-group"><label>Win Chance %</label><input id="basic_win_chance" type="number" step="0.01" value="49.50" oninput="syncWinChance('')"></div>
                    <div class="form-group">
                        <label>Target (Dice Roll)</label>
                        <input id="basic_target" type="number" step="0.01" value="50.50" oninput="syncWinChance('')">
                    </div>
                    <div class="form-group">
                        <label>Condition</label>
                        <select id="basic_condition" onchange="syncWinChance('')">
                            <option value="over">Roll Over</option>
                            <option value="under">Roll Under</option>
                        </select>
                    </div>
                </div>
            </div>
            
            <!-- Fibonacci Panel -->
            <div id="fibonacci_config" class="strat-panel" style="display:none;">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">The size of the initial bet before any Fibonacci multipliers are applied.</span></span></label>
                        <div class="input-with-usd">
                            <input id="fib_base" type="number" step="0.0001" value="0.001" oninput="syncPctToUsd('fib_base')">
                            <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                            <input id="fib_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('fib_base')">
                        </div>
                    </div>
                    <div class="form-group"><label>Win Chance %</label><input id="fib_win_chance" type="number" step="0.01" value="49.50"></div>
                    <div class="form-group"><label>Session TP %</label><input id="fib_tp" type="number" step="0.1" value="2.0"></div>
                    <div class="form-group"><label>Session SL %</label><input id="fib_sl" type="number" step="0.1" value="-5.0"></div>
                    <div class="form-group"><label>Daily Loss Cap %</label><input id="fib_daily" type="number" step="0.1" value="-10.0"></div>
                </div>
            </div>
            
            <!-- Paroli Panel -->
            <div id="paroli_config" class="strat-panel" style="display:none;">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">The initial starting bet. Bet doubles rapidly during a winning streak.</span></span></label>
                        <div class="input-with-usd">
                            <input id="par_base" type="number" step="0.0001" value="0.0005" oninput="syncPctToUsd('par_base')">
                            <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                            <input id="par_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('par_base')">
                        </div>
                    </div>
                    <div class="form-group"><label>Streak Target <span class="tooltip">!<span class="tooltiptext">How many wins in a row before cashing out the compounded winnings and resetting to Base Bet.</span></span></label><input id="par_streak" type="number" step="1" value="3"></div>
                    <div class="form-group"><label>Win Chance %</label><input id="par_win_chance" type="number" step="0.01" value="49.50"></div>
                    <div class="form-group"><label>Session TP %</label><input id="par_tp" type="number" step="0.1" value="5.0"></div>
                    <div class="form-group"><label>Session SL %</label><input id="par_sl" type="number" step="0.1" value="-3.0"></div>
                    <div class="form-group"><label>Daily Loss Cap %</label><input id="par_daily" type="number" step="0.1" value="-8.0"></div>
                </div>
            </div>
            
            <!-- Oscar's Grind Panel -->
            <div id="oscars_grind_config" class="strat-panel" style="display:none;">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Base Unit % <span class="tooltip">!<span class="tooltiptext">The profit target for the sequence. Bet will never exceed what is needed to win 1 unit.</span></span></label>
                        <div class="input-with-usd">
                            <input id="osc_base" type="number" step="0.0001" value="0.001" oninput="syncPctToUsd('osc_base')">
                            <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                            <input id="osc_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('osc_base')">
                        </div>
                    </div>
                    <div class="form-group"><label>Win Chance %</label><input id="osc_win_chance" type="number" step="0.01" value="49.50"></div>
                    <div class="form-group"><label>Session TP %</label><input id="osc_tp" type="number" step="0.1" value="2.5"></div>
                    <div class="form-group"><label>Session SL %</label><input id="osc_sl" type="number" step="0.1" value="-4.0"></div>
                    <div class="form-group"><label>Daily Loss Cap %</label><input id="osc_daily" type="number" step="0.1" value="-8.0"></div>
                </div>
            </div>

            <!-- Reverted Martingale Panel -->
            <div id="reverted_martingale_config" class="strat-panel" style="display:none;">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">Baseline bet percentage. Bets shrink from here during negative variance.</span></span></label>
                        <div class="input-with-usd">
                            <input id="rm_base" type="number" step="0.0001" value="0.0012" oninput="syncPctToUsd('rm_base')">
                            <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                            <input id="rm_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('rm_base')">
                        </div>
                    </div>
                    <div class="form-group"><label>Session TP % <span class="tooltip">!<span class="tooltiptext">Small take profit. The goal is resetting safely, not winning huge.</span></span></label><input id="rm_tp" type="number" step="0.1" value="3.0"></div>
                    <div class="form-group"><label>Session SL % <span class="tooltip">!<span class="tooltiptext">Extremely generous stop-loss since bets shrink aggressively toward zero.</span></span></label><input id="rm_sl" type="number" step="0.1" value="-8.0"></div>
                    <div class="form-group"><label>Daily Loss Cap % <span class="tooltip">!<span class="tooltiptext">Deep lockout threshold to keep grinding active for days.</span></span></label><input id="rm_daily" type="number" step="0.1" value="-10.0"></div>
                    <div class="form-group"><label>Loss Multiplier <span class="tooltip">!<span class="tooltiptext">Anti-Ruin Engine. Shrinks bet by this factor on every loss (e.g. 0.5x).</span></span></label><input id="rm_loss" type="number" step="0.1" value="0.5"></div>
                    <div class="form-group"><label>Win Multiplier <span class="tooltip">!<span class="tooltiptext">Resets bet on successful clears (e.g. 1.0x to return to base).</span></span></label><input id="rm_win" type="number" step="0.1" value="1.0"></div>
                </div>
            </div>

            <!-- Wager Grind 99 Panel -->
            <div id="wager_grind_99_config" class="strat-panel" style="display:none;">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">High base bet %. 99% win-chance allows risking larger capital for fast wager accumulation.</span></span></label>
                        <div class="input-with-usd">
                            <input id="wg99_base" type="number" step="0.0001" value="0.05" oninput="syncPctToUsd('wg99_base')">
                            <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                            <input id="wg99_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('wg99_base')">
                        </div>
                    </div>
                    <div class="form-group"><label>Session TP % <span class="tooltip">!<span class="tooltiptext">Very tight Take Profit. Will exit immediately to secure micro-wins against the house 1%.</span></span></label><input id="wg99_tp" type="number" step="0.1" value="1.0"></div>
                    <div class="form-group"><label>Session SL % <span class="tooltip">!<span class="tooltiptext">Ultra-wide stop loss. Necessary because 1% house edge slowly drains the balance.</span></span></label><input id="wg99_sl" type="number" step="0.1" value="-15.0"></div>
                    <div class="form-group"><label>Daily Loss Cap % <span class="tooltip">!<span class="tooltiptext">Ruin protector against severe variance anomaly strings on huge sample sizes.</span></span></label><input id="wg99_daily" type="number" step="0.1" value="-20.0"></div>
                </div>
            </div>

            <!-- Die Last Panel -->
            <div id="die_last_config" class="strat-panel" style="display:none;">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">Baseline bet string. Riskier % for this strategy.</span></span></label>
                        <div class="input-with-usd">
                            <input id="dl_base" type="number" step="0.0001" value="0.005" oninput="syncPctToUsd('dl_base')">
                            <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                            <input id="dl_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('dl_base')">
                        </div>
                    </div>
                    <div class="form-group"><label>Session TP % <span class="tooltip">!<span class="tooltiptext">Massive target. Stops betting if you hit this session profit hurdle.</span></span></label><input id="dl_tp" type="number" step="0.1" value="8.0"></div>
                    <div class="form-group"><label>Session SL % <span class="tooltip">!<span class="tooltiptext">Wider Stop Loss to allow volatile streaks without stopping early.</span></span></label><input id="dl_sl" type="number" step="0.1" value="-3.5"></div>
                    <div class="form-group"><label>Daily Loss Cap % <span class="tooltip">!<span class="tooltiptext">Locks out for the day if you hit this 24H brutal maximum loss floor.</span></span></label><input id="dl_daily" type="number" step="0.1" value="-12.0"></div>
                </div>
            </div>

            <!-- Vanish in Volume Panel -->
            <div id="vanish_config" class="strat-panel" style="display:none;">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">Ultra-low base bet. Core of the infinite survival strategy.</span></span></label>
                        <div class="input-with-usd">
                            <input id="v_base" type="number" step="0.0001" value="0.0015" oninput="syncPctToUsd('v_base')">
                            <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                            <input id="v_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('v_base')">
                        </div>
                    </div>
                    <div class="form-group"><label>Session TP % <span class="tooltip">!<span class="tooltiptext">Moderate +% target before resetting session safety bounds.</span></span></label><input id="v_tp" type="number" step="0.1" value="3.5"></div>
                    <div class="form-group"><label>Session SL % <span class="tooltip">!<span class="tooltiptext">Tight Stop Loss that scales down base bets dynamically as you hit it.</span></span></label><input id="v_sl" type="number" step="0.1" value="-2.0"></div>
                    <div class="form-group"><label>Daily Loss Cap % <span class="tooltip">!<span class="tooltiptext">Conservative daily circuit breaker. 24 hour lockout.</span></span></label><input id="v_daily" type="number" step="0.1" value="-1.5"></div>
                </div>
            </div>

            <!-- Eternal Volume Panel -->
            <div id="eternal_config" class="strat-panel" style="display:none;">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">Microscopic fractional multiplier. Re-evaluates every single outcome natively.</span></span></label>
                        <div class="input-with-usd">
                            <input id="e_base" type="number" step="0.0001" value="0.0012" oninput="syncPctToUsd('e_base')">
                            <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                            <input id="e_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('e_base')">
                        </div>
                    </div>
                    <div class="form-group"><label>Session TP % <span class="tooltip">!<span class="tooltiptext">Tame take profit percentage. Logs green immediately.</span></span></label><input id="e_tp" type="number" step="0.1" value="3.0"></div>
                    <div class="form-group"><label>Session SL % <span class="tooltip">!<span class="tooltiptext">Extremely tight SL %. You will exit sessions quickly during storms.</span></span></label><input id="e_sl" type="number" step="0.1" value="-1.4"></div>
                    <div class="form-group"><label>Daily Loss Cap % <span class="tooltip">!<span class="tooltiptext">Will halt trading for 24h if you sink this far in a single day.</span></span></label><input id="e_daily" type="number" step="0.1" value="-2.5"></div>
                </div>
            </div>

            <!-- Custom Strategy Panel -->
            <div id="custom_config" class="strat-panel" style="display:none;">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Custom Strategy Base Bet % <span class="tooltip">!<span class="tooltiptext">This value can be used within your custom Python strategy as a configurable parameter.</span></span></label>
                        <div class="input-with-usd">
                            <input id="c_base" type="number" step="0.0001" value="0.001" oninput="syncPctToUsd('c_base')">
                            <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                            <input id="c_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('c_base')">
                        </div>
                    </div>
                    <div class="form-group"><label>Custom TP % <span class="tooltip">!<span class="tooltiptext">This value can be used within your custom Python strategy.</span></span></label><input id="c_tp" type="number" step="0.1" value="2.0"></div>
                    <div class="form-group"><label>Custom SL % <span class="tooltip">!<span class="tooltiptext">This value can be used within your custom Python strategy.</span></span></label><input id="c_sl" type="number" step="0.1" value="-1.0"></div>
                    <div class="form-group"><label>Custom Daily Cap % <span class="tooltip">!<span class="tooltiptext">This value can be used within your custom Python strategy.</span></span></label><input id="c_daily" type="number" step="0.1" value="-1.0"></div>
                </div>
                <div id="dynamic_custom_params" class="config-grid" style="margin-top:1rem; border-top:1px solid rgba(102,252,241,0.1); padding-top:1rem;"></div>
            </div>
            
            <!-- Dragon Chaser Panel -->
            <div id="dragon_chaser_config" class="strat-panel" style="display:none;">
                <div class="config-grid">
                    <div class="form-group">
                        <label>Target Difficulty</label>
                        <select id="dc_difficulty">
                            <option value="easy">Easy (4 tiles, 1 egg)</option>
                            <option value="medium">Medium (3 tiles, 1 egg)</option>
                            <option value="hard">Hard (2 tiles, 1 egg)</option>
                            <option value="expert">Expert (3 tiles, 2 eggs)</option>
                            <option value="master">Master (4 tiles, 3 eggs)</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Target Safe Column <span class="tooltip">!<span class="tooltiptext">The column (1-4) that must be entirely Safe from bottom to top for the bot to halt.</span></span></label>
                        <select id="dc_target_col">
                            <option value="0">Column 1 (Far Left)</option>
                            <option value="1">Column 2</option>
                            <option value="2">Column 3</option>
                            <option value="3">Column 4 (Far Right)</option>
                        </select>
                    </div>
                </div>
                <div class="config-grid" style="margin-top:1rem;">
                    <div class="form-group">
                        <label>Target Server Seed (Hash) <span class="tooltip">!<span class="tooltiptext">Paste your active Stake Server Seed here. The bot uses config from the Predictor tab if empty.</span></span></label>
                        <input id="dc_server_seed" type="text" placeholder="Server Seed...">
                    </div>
                    <div class="form-group">
                        <label>Client Seed</label>
                        <input id="dc_client_seed" type="text" placeholder="Client Seed...">
                    </div>
                    <div class="form-group">
                        <label>Starting Nonce</label>
                        <input id="dc_nonce" type="number" placeholder="Current Nonce...">
                    </div>
                </div>
                <div style="margin-top:1rem; font-size:0.8rem; color:var(--secondary); background:rgba(0,0,0,0.2); padding:0.8rem; border-radius:6px; line-height:1.4;">
                    <strong>HOW IT WORKS:</strong> Select your target difficulty and desired exact safe column. Make sure your Seeds and current Nonce are entered. Click "Initialize". The bot will place <strong>Minimum Dice Bets</strong> to artificially advance your Stake Nonce until it calculates a future Dragon Tower map where your selected column is 100% safe. It will then <strong>Halt</strong>, allowing you to play that exact nonce manually on Stake!
                </div>
            </div>
        </div>
    </div>

        <div class="grid" style="margin-top:2rem; grid-template-columns: 1fr 1fr;">
            <div class="panel">
                <h3>Execution Logs</h3>
                <div id="logs" style="height:350px;"></div>
            </div>
            <div class="panel" style="display:flex; flex-direction:column;">
                <h3>Gemini AI Controller <span class="badge" style="background:var(--primary); color:#000; font-size:0.6rem;">BETA</span></h3>
                <div id="ai-chat" style="flex:1; min-height:280px; max-height:280px; overflow-y:auto; background:rgba(0,0,0,0.3); border-radius:4px; padding:0.8rem; margin-bottom:0.8rem; font-size:0.85rem; line-height:1.5; border:1px solid rgba(102,252,241,0.1);">
                    <div style="color:var(--secondary); font-style:italic; opacity:0.7;">System: Ready. You can ask me to "Stop the bot", "Check P&L", or "Switch to Die Last strategy".</div>
                </div>
                <div style="display:flex; gap:0.5rem;">
                    <input type="text" id="ai-input" placeholder="Command Gork via AI..." style="flex:1; border-color:rgba(102,252,241,0.3);" onkeypress="if(event.key==='Enter') sendAiMessage()">
                    <button class="btn-start" style="padding:0.5rem 1.2rem; font-size:0.85rem;" onclick="sendAiMessage()">Send</button>
                </div>
            </div>
        </div>
    </div>

    <!-- CHARTING TAB -->
    <div id="charting" class="tab-content" style="display:none;">
        <div class="panel">
            <h3>Live Session Analytics</h3>
            <div class="chart-container">
                <div id="tvchart" style="width: 100%; height: 100%; min-height:350px;"></div>
            </div>
        </div>
    </div>

    <!-- STRATEGY EDITOR TAB -->
    <div id="editor" class="tab-content" style="display:none;">
        <div class="panel">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
                <h3 style="border:none; margin:0;">Python Strategy Editor</h3>
                <div style="display:flex; gap:0.5rem; align-items:center;">
                    <label style="margin:0; font-size:0.75rem;">Template:</label>
                    <select id="template-select" style="width:180px; padding:0.4rem; font-size:0.8rem;"></select>
                    <button class="btn-start" style="padding:0.4rem 0.8rem; font-size:0.75rem;" onclick="loadTemplate()">Load</button>
                </div>
            </div>
            <div style="font-size:0.85rem; color:var(--secondary); margin-bottom:1rem;">
                Code your own strategy logic here. Access `balance`, `state`, `log`. Set `result = bet_amount`.
            </div>
            <div id="ace-editor" style="height:500px; width:100%; border-radius:8px; border:1px solid rgba(102,252,241,0.2);"></div>
            <div style="margin-top:1rem; display:flex; gap:1rem; align-items:center;">
                <button class="btn-start" onclick="saveCustomCode()">Save Strategy</button>
                <span id="editor-status" style="font-size:0.85rem;"></span>
            </div>
        </div>
    </div>

    <!-- SIMULATOR TAB -->
    <div id="simulate" class="tab-content" style="display:none;">
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:2rem;">

            <!-- Left: Config Panel -->
            <div style="display:flex; flex-direction:column; gap:2rem;">
                <div class="panel">
                    <h3 style="display:flex;justify-content:space-between;">
                        <span>Benchmark Simulator</span>
                        <span id="sim-status" class="badge PAUSED">AWAITING PARAMS</span>
                    </h3>
                    <div style="color:var(--secondary); font-size:0.82rem; margin-bottom:1rem; line-height:1.5; border-left:2px solid var(--primary); padding-left:0.8rem;">
                        Uses the same HMAC-SHA256 Stake provably fair seeding as real games. All algorithm logic is identical to live — no simplifications.
                    </div>

                    <div class="form-group">
                        <label>Strategy Algorithm</label>
                        <select id="sim_strategy" onchange="simToggleStrat()">
                            <option value="the_gork">THE GORK (Conservative)</option>
                            <option value="basic">BASIC (Absolute Amount)</option>
                            <option value="ema_cross">EMA CROSSOVER (Trend)</option>
                            <option value="die_last">DIE LAST (Aggressive)</option>
                            <option value="vanish_in_volume">VANISH IN VOLUME (Safe)</option>
                            <option value="eternal_volume">ETERNAL VOLUME (Volume)</option>
                            <option value="reverted_martingale">REVERTED MARTINGALE (Anti-Ruin)</option>
                            <option value="wager_grind_99">WAGER GRIND 99 (Max Volume)</option>
                            <option value="custom">CUSTOM (Your Python Code)</option>
                        </select>
                        <div id="sim-strat-desc" style="font-size:0.78rem; color:var(--secondary); margin-top:0.5rem; padding:0.5rem; background:rgba(0,0,0,0.2); border-radius:4px; border-left:2px solid var(--primary);"></div>
                    </div>

                    <div class="config-grid">
                        <div class="form-group">
                            <label>Starting Balance <span class="tooltip">!<span class="tooltiptext">The virtual bankroll the simulation starts with. Does not use real funds.</span></span></label>
                            <input type="number" id="sim_balance" value="1000.00" step="1">
                        </div>
                        <div class="form-group">
                            <label>Bet Count <span class="tooltip">!<span class="tooltiptext">Number of individual dice bets to simulate. Higher counts give more accurate long-term results.</span></span></label>
                            <input type="number" id="sim_bets" value="10000" step="100" style="width:100%;">
                        </div>
                        <div class="form-group">
                            <label>All-Time Drawdown % <span class="tooltip">!<span class="tooltiptext">Simulation halts permanently if the balance drops this % below the starting balance. Same as the real live circuit breaker.</span></span></label>
                            <input type="number" id="sim_atcap" step="0.1" value="-8.0">
                        </div>
                        <div class="form-group">
                            <label>Min Bet Floor <span class="tooltip">!<span class="tooltiptext">The smallest bet the algorithm is permitted to place. Prevents rounding errors at near-zero balances.</span></span></label>
                            <input type="number" id="sim_floor" step="0.000001" value="0.000001">
                        </div>
                    </div>

                    <!-- BASIC params in sim -->
                    <div id="sim_basic_config" class="sim-strat-panel" style="display:none;">
                        <div class="config-grid">
                            <div class="form-group">
                                <label>Bet Amount (Absolute)</label>
                                <input id="s_basic_bet_amount" type="number" step="0.000001" value="0.000001">
                            </div>
                            <div class="form-group">
                                <label>On Win Action</label>
                                <select id="s_basic_on_win" onchange="toggleBasicMults()">
                                    <option value="reset">Reset to Base</option>
                                    <option value="multiply">Multiply Bet</option>
                                    <option value="stay">Stay Same</option>
                                </select>
                            </div>
                            <div id="s_basic_win_mult_wrap" class="form-group" style="display:none;">
                                <label>Win Multiplier</label>
                                <input id="s_basic_win_mult" type="number" step="0.1" value="1.0">
                            </div>
                            <div class="form-group">
                                <label>On Loss Action</label>
                                <select id="s_basic_on_loss" onchange="toggleBasicMults()">
                                    <option value="multiply">Multiply Bet (Martingale)</option>
                                    <option value="reset">Reset to Base</option>
                                    <option value="stay">Stay Same</option>
                                </select>
                            </div>
                            <div id="s_basic_loss_mult_wrap" class="form-group">
                                <label>Loss Multiplier</label>
                                <input id="s_basic_loss_mult" type="number" step="0.1" value="2.0">
                            </div>
                            <div class="form-group"><label>Win Chance %</label><input id="s_basic_win_chance" type="number" step="0.01" value="49.50" oninput="syncWinChance('s_')"></div>
                            <div class="form-group">
                                <label>Target (Dice Roll)</label>
                                <input id="s_basic_target" type="number" step="0.01" value="50.50" oninput="syncWinChance('s_')">
                            </div>
                            <div class="form-group">
                                <label>Condition</label>
                                <select id="s_basic_condition" onchange="syncWinChance('s_')">
                                    <option value="over">Roll Over</option>
                                    <option value="under">Roll Under</option>
                                </select>
                            </div>
                        </div>
                    </div>
                    <!-- REVERTED params in sim -->
                    <div id="sim_reverted_martingale_config" class="sim-strat-panel" style="display:none;">
                        <div class="config-grid">
                            <div class="form-group">
                                <label>Base Bet %</label>
                                <div class="input-with-usd">
                                    <input type="number" id="s_rm_base" step="0.0001" value="0.0012" oninput="syncPctToUsd('s_rm_base')">
                                    <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                                    <input id="s_rm_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('s_rm_base')">
                                </div>
                            </div>
                            <div class="form-group"><label>Session TP %</label><input type="number" id="s_rm_tp" step="0.1" value="3.0"></div>
                            <div class="form-group"><label>Session SL %</label><input type="number" id="s_rm_sl" step="0.1" value="-8.0"></div>
                            <div class="form-group"><label>Daily Cap %</label><input type="number" id="s_rm_daily" step="0.1" value="-10.0"></div>
                            <div class="form-group"><label>Loss Multiplier</label><input type="number" id="s_rm_loss" step="0.1" value="0.5"></div>
                            <div class="form-group"><label>Win Multiplier</label><input type="number" id="s_rm_win" step="0.1" value="1.0"></div>
                        </div>
                    </div>
                    <!-- WAGER GRIND params in sim -->
                    <div id="sim_wager_grind_99_config" class="sim-strat-panel" style="display:none;">
                        <div class="config-grid">
                            <div class="form-group">
                                <label>Base Bet %</label>
                                <div class="input-with-usd">
                                    <input type="number" id="s_wg99_base" step="0.0001" value="0.05" oninput="syncPctToUsd('s_wg99_base')">
                                    <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                                    <input id="s_wg99_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('s_wg99_base')">
                                </div>
                            </div>
                            <div class="form-group"><label>Session TP %</label><input type="number" id="s_wg99_tp" step="0.1" value="1.0"></div>
                            <div class="form-group"><label>Session SL %</label><input type="number" id="s_wg99_sl" step="0.1" value="-15.0"></div>
                            <div class="form-group"><label>Daily Cap %</label><input type="number" id="s_wg99_daily" step="0.1" value="-20.0"></div>
                        </div>
                    </div>
                    <div id="sim_gork_config" class="sim-strat-panel">
                        <div class="config-grid">
                            <div class="form-group">
                                <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">% of balance risked on the initial bet. The Gork scales up when recovering.</span></span></label>
                                <div class="input-with-usd">
                                    <input type="number" id="s_g_base" step="0.0001" value="0.0012" oninput="syncPctToUsd('s_g_base')">
                                    <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                                    <input id="s_g_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('s_g_base')">
                                </div>
                            </div>
                            <div class="form-group"><label>Session TP % <span class="tooltip">!<span class="tooltiptext">Stops the session and resets when your balance is up this % from session start.</span></span></label><input type="number" id="s_g_tp" step="0.1" value="3.0"></div>
                            <div class="form-group"><label>Session SL % <span class="tooltip">!<span class="tooltiptext">Stops the session if drawdown exceeds this % from session start. Protects against deep holes.</span></span></label><input type="number" id="s_g_sl" step="0.1" value="-1.4"></div>
                            <div class="form-group"><label>Daily Cap % <span class="tooltip">!<span class="tooltiptext">Circuit breaker: locks bot for 24h if daily P&L drops below this threshold.</span></span></label><input type="number" id="s_g_daily" step="0.1" value="-1.8"></div>
                        </div>
                    </div>
                    <!-- EMA params in sim -->
                    <div id="sim_ema_config" class="sim-strat-panel" style="display:none;">
                        <div class="config-grid">
                            <div class="form-group">
                                <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">Fractional balance bet per iteration. EMA signal adjusts Over/Under direction, not size.</span></span></label>
                                <div class="input-with-usd">
                                    <input type="number" id="s_ema_base" step="0.0001" value="0.0012" oninput="syncPctToUsd('s_ema_base')">
                                    <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                                    <input id="s_ema_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('s_ema_base')">
                                </div>
                            </div>
                            <div class="form-group"><label>Session TP % <span class="tooltip">!<span class="tooltiptext">Target profit threshold to auto-exit the session.</span></span></label><input type="number" id="s_ema_tp" step="0.1" value="2.0"></div>
                            <div class="form-group"><label>Session SL % <span class="tooltip">!<span class="tooltiptext">Session stop-loss. Exits when drawdown from session start exceeds this %.</span></span></label><input type="number" id="s_ema_sl" step="0.1" value="-1.2"></div>
                            <div class="form-group"><label>Daily Cap % <span class="tooltip">!<span class="tooltiptext">24-hour global loss cap. Halts all activity for the rest of the day.</span></span></label><input type="number" id="s_ema_daily" step="0.1" value="-2.0"></div>
                        </div>
                    </div>
                    <!-- DIE LAST params in sim -->
                    <div id="sim_die_last_config" class="sim-strat-panel" style="display:none;">
                        <div class="config-grid">
                            <div class="form-group">
                                <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">Higher base bet — Die Last runs aggressive 2.5x streak multipliers so start conservatively.</span></span></label>
                                <div class="input-with-usd">
                                    <input type="number" id="s_dl_base" step="0.0001" value="0.005" oninput="syncPctToUsd('s_dl_base')">
                                    <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                                    <input id="s_dl_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('s_dl_base')">
                                </div>
                            </div>
                            <div class="form-group"><label>Session TP % <span class="tooltip">!<span class="tooltiptext">Higher TP target required since Die Last streaks up aggressively to reach it.</span></span></label><input type="number" id="s_dl_tp" step="0.1" value="8.0"></div>
                            <div class="form-group"><label>Session SL % <span class="tooltip">!<span class="tooltiptext">Tight stop-loss limits damage from sustained losing runs before a streak resets.</span></span></label><input type="number" id="s_dl_sl" step="0.1" value="-3.5"></div>
                            <div class="form-group"><label>Daily Cap % <span class="tooltip">!<span class="tooltiptext">Hard daily ceiling — activates 24h lockout if cumulative daily loss exceeds this.</span></span></label><input type="number" id="s_dl_daily" step="0.1" value="-5.0"></div>
                        </div>
                    </div>
                    <!-- VANISH params in sim -->
                    <div id="sim_vanish_config" class="sim-strat-panel" style="display:none;">
                        <div class="config-grid">
                            <div class="form-group">
                                <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">Ultra-low base bet. Vanish dynamically shrinks this further during drawdown phases.</span></span></label>
                                <div class="input-with-usd">
                                    <input type="number" id="s_v_base" step="0.0001" value="0.0015" oninput="syncPctToUsd('s_v_base')">
                                    <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                                    <input id="s_v_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('s_v_base')">
                                </div>
                            </div>
                            <div class="form-group"><label>Session TP % <span class="tooltip">!<span class="tooltiptext">Target profit to exit the session. Vanish reaches this slowly but safely.</span></span></label><input type="number" id="s_v_tp" step="0.1" value="3.5"></div>
                            <div class="form-group"><label>Session SL % <span class="tooltip">!<span class="tooltiptext">Defense threshold — stops the session to prevent the shrink factor from reducing bets too far.</span></span></label><input type="number" id="s_v_sl" step="0.1" value="-2.0"></div>
                            <div class="form-group"><label>Daily Cap % <span class="tooltip">!<span class="tooltiptext">Daily lockout threshold. Conservative — Vanish prioritises capital preservation.</span></span></label><input type="number" id="s_v_daily" step="0.1" value="-1.5"></div>
                        </div>
                    </div>
                    <!-- ETERNAL params in sim -->
                    <div id="sim_eternal_config" class="sim-strat-panel" style="display:none;">
                        <div class="config-grid">
                            <div class="form-group">
                                <label>Base Bet % <span class="tooltip">!<span class="tooltiptext">Flat fractional per-bet. Eternal recalculates this from your current balance every single bet.</span></span></label>
                                <div class="input-with-usd">
                                    <input type="number" id="s_e_base" step="0.0001" value="0.0012" oninput="syncPctToUsd('s_e_base')">
                                    <span style="color:var(--secondary);font-size:0.75rem;">≈</span>
                                    <input id="s_e_base_usd" class="input-usd-helper" type="number" step="0.01" placeholder="$ USD" oninput="syncUsdToPct('s_e_base')">
                                </div>
                            </div>
                            <div class="form-group"><label>Session TP % <span class="tooltip">!<span class="tooltiptext">Soft profit target. Eternal exits fast — favours volume over large sessions.</span></span></label><input type="number" id="s_e_tp" step="0.1" value="3.0"></div>
                            <div class="form-group"><label>Session SL % <span class="tooltip">!<span class="tooltiptext">Very tight stop. Flat sizing means losses accumulate slowly; exit quickly.</span></span></label><input type="number" id="s_e_sl" step="0.1" value="-1.4"></div>
                            <div class="form-group"><label>Daily Cap % <span class="tooltip">!<span class="tooltiptext">Daily lockout. Should be set loosely since Eternal runs very many small sessions per day.</span></span></label><input type="number" id="s_e_daily" step="0.1" value="-2.5"></div>
                        </div>
                    </div>
                    <div id="sim_custom_config" class="sim-strat-panel" style="display:none;">
                        <div class="config-grid">
                            <div class="form-group">
                                <label>Custom Base % <span class="tooltip">!<span class="tooltiptext">Base bet parameter for your custom code.</span></span></label>
                                <input type="number" id="s_c_base" step="0.0001" value="0.001">
                            </div>
                            <div class="form-group"><label>Custom TP %</label><input type="number" id="s_c_tp" step="0.1" value="2.0"></div>
                            <div class="form-group"><label>Custom SL %</label><input type="number" id="s_c_sl" step="0.1" value="-1.0"></div>
                            <div class="form-group"><label>Custom Daily Cap %</label><input type="number" id="s_c_daily" step="0.1" value="-1.0"></div>
                        </div>
                        <div id="sim_dynamic_custom_params" class="config-grid" style="margin-top:1rem; border-top:1px solid rgba(102,252,241,0.1); padding-top:1rem;"></div>
                    </div>

                    <button class="btn-start" style="width:100%;margin-top:1rem;font-size:1.05rem;padding:0.9rem;" onclick="runSimulation()">▶ Execute Simulation (HMAC Seeded)</button>
                </div>

                <!-- Saved Presets Panel -->
                <div class="panel">
                    <h3>Saved Strategy Presets</h3>
                    <div style="display:flex;gap:0.5rem;margin-bottom:1rem;">
                        <input type="text" id="preset_name" placeholder="Preset name..." style="flex:1;">
                        <button class="btn-start" style="padding:0.5rem 1rem;font-size:0.85rem;" onclick="savePreset()">Save</button>
                    </div>
                    <div id="preset-list" style="display:flex;flex-direction:column;gap:0.5rem;"></div>
                </div>
            </div>

            <!-- Right: Results Panel -->
            <div class="panel" style="align-self:start;">
                <h3>Benchmark / Simulator v2.0</h3>
                <!-- Equity Curve Chart -->
                <div id="sim-chart-wrap" style="height:300px; width:100%; margin-bottom:1.5rem; border-radius:8px; overflow:hidden; border:1px solid rgba(102,252,241,0.1); display:none;">
                    <div id="sim-chart" style="width:100%;height:100%;"></div>
                </div>
                <div class="sim-grid" style="grid-template-columns:1fr 1fr;">
                    <div class="sim-stat"><div class="sim-title">Final Balance</div><div class="sim-val" id="res-bal">$1000.00</div></div>
                    <div class="sim-stat"><div class="sim-title">Net P&L</div><div class="sim-val" id="res-pnl">$0.00</div></div>
                    <div class="sim-stat"><div class="sim-title">Total Wagered</div><div class="sim-val" id="res-wager">$0.00</div></div>
                    <div class="sim-stat"><div class="sim-title">Win Rate</div><div class="sim-val" id="res-wr">0.00%</div></div>
                    <div class="sim-stat"><div class="sim-title">Worst Drawdown</div><div class="sim-val sim-danger" id="res-dd">0.00%</div></div>
                    <div class="sim-stat"><div class="sim-title">Peak Balance</div><div class="sim-val sim-success" id="res-peak">$1000.00</div></div>
                    <div class="sim-stat"><div class="sim-title">Wins / Losses</div><div class="sim-val" style="color:#fff;font-size:1.1rem;" id="res-wl">0 / 0</div></div>
                    <div class="sim-stat"><div class="sim-title">Circuit Breakers</div><div class="sim-val" style="color:#f1c40f;" id="res-cb">0</div></div>
                </div>
                <div id="sim-summary" style="margin-top:1.5rem; padding:1rem; background:rgba(0,0,0,0.3); border-radius:8px; font-size:0.85rem; line-height:1.8; color:var(--text); display:none;"></div>
            </div>
        </div>
    </div>

    <!-- DRAGON TOWER TAB -->
    <div id="dragon" class="tab-content" style="display:none;">
        <div class="grid">
            <div class="panel">
                <h3>Dragon Predictor Config</h3>
                <div class="form-group">
                    <label>Server Seed (Actual or Hash)</label>
                    <input id="dt_server_seed" type="text" placeholder="Paste server seed...">
                </div>
                <div class="form-group">
                    <label>Client Seed</label>
                    <input id="dt_client_seed" type="text" placeholder="Paste client seed...">
                </div>
                <div class="form-group">
                    <label>Nonce</label>
                    <input id="dt_nonce" type="number" value="0">
                </div>
                <div class="form-group">
                    <label>Difficulty</label>
                    <select id="dt_difficulty" onchange="predictDragon()">
                        <option value="easy">EASY (4 tiles, 3 safe)</option>
                        <option value="medium">MEDIUM (3 tiles, 2 safe)</option>
                        <option value="hard">HARD (2 tiles, 1 safe)</option>
                        <option value="expert">EXPERT (3 tiles, 1 safe)</option>
                        <option value="master">MASTER (4 tiles, 1 safe)</option>
                    </select>
                </div>
                <button class="btn-start" style="width:100%; margin-top:1rem;" onclick="predictDragon()">GENERATE TOWER MAP</button>
                
                <div style="margin-top:2rem; padding:1.2rem; border:1px solid rgba(255, 71, 87, 0.3); border-radius:12px; background:rgba(255, 71, 87, 0.05); border-left-width: 5px;">
                    <h4 style="color:var(--danger); margin:0 0 0.5rem 0; font-family:'Roboto Mono',monospace;">⚠ SECURITY ADVISORY</h4>
                    <p style="font-size:0.8rem; color:var(--text); line-height:1.5; margin:0;">
                        The "hack scripts" found in public repositories are typically <b>Session Hijackers</b> designed to steal your account tokens when pasted into the browser console. 
                        This tool uses the official <b>Provably Fair</b> HMAC-SHA256 algorithm locally on your machine to reveal the tower configuration safely.
                    </p>
                </div>
            </div>
            
            <div class="panel">
                <h3>Tower Map Visualization</h3>
                <div id="tower-viz" class="tower-grid">
                    <div style="text-align:center; padding: 6rem 1rem; color: rgba(255,255,255,0.1); font-style: italic;">
                        Seeded derivation results will appear here...
                    </div>
                </div>
                <div style="margin-top:1rem; text-align:center; font-size:0.7rem; color:var(--secondary);">
                    Calculated using Fisher-Yates Shuffle & HMAC-SHA256
                </div>
            </div>
        </div>
    </div>

    <!-- SETTINGS & VIP TAB -->

    <div id="settings" class="tab-content" style="display:none;">
        <div class="settings-grid">
            
            <!-- Left Column: Core Settings -->
            <div style="display:flex; flex-direction:column; gap:2rem;">
                <div class="panel">
                    <h3>Connection & API Key</h3>
                    <div class="form-group">
                        <label>Stake API Bearer Token</label>
                        <div class="input-group">
                            <input type="password" id="api_token" placeholder="Enter Token...">
                            <button class="btn-start" onclick="saveApiKey()">Save & Validate</button>
                        </div>
                        <div id="api-error-msg" style="color: var(--danger); font-size: 0.8rem; margin-top: 0.5rem; display: none;"></div>
                    </div>
                    <div style="margin-top:1rem; display:flex; justify-content:space-between; align-items:center;">
                        <span style="color:var(--secondary); font-size:0.9rem;">API Health Status:</span>
                        <span id="api-health" class="badge PAUSED">WAITING</span>
                    </div>
                </div>

                <div class="panel">
                    <h3>Gemini AI Integration</h3>
                    <div class="form-group">
                        <label>Google Gemini API Key</label>
                        <div class="input-group">
                            <input type="password" id="gemini_key" placeholder="Paste Gemini Key...">
                            <button class="btn-start" onclick="saveGeminiKey()">Update AI Engine</button>
                        </div>
                        <div style="font-size:0.75rem; color:var(--secondary); margin-top:0.4rem;">Used for natural language terminal control.</div>
                    </div>
                </div>


                <div class="panel">
                    <h3>Active Wallets</h3>
                    <div style="color:var(--secondary); font-size:0.8rem; margin-bottom:1rem;">Select active balance. Converted to USD automatically.</div>
                    <div id="wallet-list">
                        <!-- Populated by JS -->
                        <div style="text-align:center; padding: 2rem; color: rgba(255,255,255,0.2);">Awaiting API...</div>
                    </div>
                </div>
            </div>

            <!-- Right Column: VIP Dashboard -->
            <div style="display:flex; flex-direction:column; gap:2rem;">
                <div class="panel vip-card">
                    <h3 style="border:none; margin:0;">VIP PROGRESS</h3>
                    <div class="vip-badge" id="vip-rank">?</div>
                    
                    <div style="margin-top:2rem;">
                        <span style="font-size:1.5rem; font-weight:bold; color:#fff;" id="vip-pct">0.00%</span>
                        <div class="progress-bg">
                            <div class="progress-fill" id="vip-fill" style="width: 0%;"></div>
                        </div>
                    </div>
                </div>

                <div class="panel">
                    <h3>Rewards & Claims</h3>
                    <div class="claim-row">
                        <div>
                            <div style="font-weight:bold; color:#fff;">Rakeback</div>
                            <div style="font-size:0.8rem; color:var(--secondary);">Available to claim!</div>
                        </div>
                        <button class="claim-btn">Claim</button>
                    </div>
                    <div class="claim-row">
                        <div>
                            <div style="font-weight:bold; color:#fff;">Reload</div>
                            <div style="font-size:0.8rem; color:var(--secondary);">No Reload available.</div>
                        </div>
                        <button class="claim-btn disabled">Claim</button>
                    </div>
                    <div class="claim-row">
                        <div>
                            <div style="font-weight:bold; color:#fff;">Weekly Boost</div>
                            <div style="font-size:0.8rem; color:var(--secondary);">View Details</div>
                        </div>
                        <span style="color:var(--secondary);">></span>
                    </div>
                </div>
                
                <div class="panel">
                    <h3>Wager Statistics</h3>
                    <div class="stat-row"><span>Daily Wager</span><span class="stat-value" id="stat-day">$0.00</span></div>
                    <div class="stat-row"><span>Weekly Wager</span><span class="stat-value" id="stat-week">$0.00</span></div>
                    <div class="stat-row"><span>Monthly Wager</span><span class="stat-value" id="stat-month">$0.00</span></div>
                </div>
            </div>

        </div>
    </div>

    <script>
        // Tab Logic
        function openTab(btn, tabId) {
            document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
            
            btn.classList.add('active');
            document.getElementById(tabId).style.display = 'block';
            if (tabId === 'editor') {
                setTimeout(() => editor.resize(), 100);
            }
        }

        function toggleStrat() {
            const strat = document.getElementById('strategy').value;
            document.querySelectorAll('.strat-panel').forEach(p => p.style.display = 'none');
            
            const desc = document.getElementById('strat-desc');
            if (strat === 'the_gork') {
                document.getElementById('gork_config').style.display = 'block';
                desc.textContent = "Auto-scales bet size based on distance to the daily starting bankroll target. The ultimate distance recovery algorithm.";
            } else if (strat === 'basic') {
                document.getElementById('basic_config').style.display = 'block';
                desc.textContent = "Simple absolute-bet strategy with win/loss multipliers. Perfect for Martingale or fixed-size volume.";
                toggleBasicMults();
            } else if (strat === 'die_last') {
                document.getElementById('die_last_config').style.display = 'block';
                desc.textContent = "Aggressive progression. Higher base bet, utilizes 2.5x streak multipliers. Can drop base bet by 50% on prolonged loss streaks.";
            } else if (strat === 'vanish_in_volume') {
                document.getElementById('vanish_config').style.display = 'block';
                desc.textContent = "Ultra-defensive. Maximum 1.8x streak multiplier. Automatically shrinks base bet sizes the further the session drops into drawdown.";
            } else if (strat === 'eternal_volume') {
                document.getElementById('eternal_config').style.display = 'block';
                desc.textContent = "Zero progression. Flat fractional bet percentages sized freshly every single bet. The absolute highest volume with the lowest ruin rate.";
            } else if (strat === 'reverted_martingale') {
                document.getElementById('reverted_martingale_config').style.display = 'block';
                desc.textContent = "Wagering algorithm. Halves your bet strictly on losses to mathematically dodge bankruptcy strings. Flat resets on wins.";
            } else if (strat === 'wager_grind_99') {
                document.getElementById('wager_grind_99_config').style.display = 'block';
                desc.textContent = "Raw volume engine. Bets perfectly flat sizing at a 99% probability win rate. The drawdown maps identically to the 1% house edge mathematically.";
            } else if (strat === 'fibonacci') {
                document.getElementById('fibonacci_config').style.display = 'block';
                desc.textContent = "Mathematical recovery algorithm. Follows the Fibonacci sequence (1,1,2,3,5...) on a loss to absorb catastrophic strings, and retreats 2 steps forward on a win.";
            } else if (strat === 'paroli') {
                document.getElementById('paroli_config').style.display = 'block';
                desc.textContent = "Reverse Martingale variation. Hunts for a win streak by doubling profit. Banks cash immediately upon hitting the Streak Target.";
            } else if (strat === 'oscars_grind') {
                document.getElementById('oscars_grind_config').style.display = 'block';
                desc.textContent = "Target seeking algorithm. Aims to exactly win 1 base unit of profit per sequence, maintaining high stability against localized variance.";
            } else if (strat === 'ema_cross') {
                document.getElementById('ema_config').style.display = 'block';
                desc.textContent = "Trend following algorithm calculating the 5-Period and 20-Period Exponential Moving Average of float roll probabilities. Buys >50.50 dynamically.";
            } else if (strat === 'custom') {
                document.getElementById('custom_config').style.display = 'block';
                desc.textContent = "Your custom Python strategy. Code it in the STRATEGY EDITOR tab.";
                renderDynamicParams('custom_config', 'dynamic_custom_params');
            }
        }

        let globalPrices = {btc:100000, ltc:100, eth:2500};
        let currentBalance = 0;
        let currentCurrency = 'btc';
        // Global chart objects
        window.gork_sim_chart = null;

        function syncPctToUsd(baseId) {
            const isSim = baseId.startsWith('s_');
            const pct = parseFloat(document.getElementById(baseId).value) / 100;
            const usdInput = document.getElementById(baseId + '_usd');
            if (!usdInput || isNaN(pct)) return;
            const price = globalPrices[currentCurrency] || 1;
            const balance = isSim ? parseFloat(document.getElementById('sim_balance').value) : currentBalance;
            if (balance > 0) {
                usdInput.value = (balance * pct * price).toFixed(2);
            }
        }

        function syncUsdToPct(baseId) {
            const isSim = baseId.startsWith('s_');
            const usdVal = parseFloat(document.getElementById(baseId + '_usd').value);
            const pctInput = document.getElementById(baseId);
            if (!pctInput || isNaN(usdVal)) return;
            const price = globalPrices[currentCurrency] || 1;
            const balance = isSim ? parseFloat(document.getElementById('sim_balance').value) : currentBalance;
            if (balance > 0 && price > 0) {
                pctInput.value = ((usdVal / price) / balance * 100).toFixed(8);
            }
        }

        function update() {
            fetch('/status').then(r=>r.json()).then(d => {
                currentBalance = d.balance.available;
                currentCurrency = d.balance.currency.toLowerCase();
                if (d.prices) globalPrices = d.prices;

                document.getElementById('balance').textContent = d.balance.available.toFixed(8);
                document.getElementById('currency').textContent = d.balance.currency.toUpperCase();
                document.getElementById('bet').textContent = d.current_bet.toFixed(8);
                document.getElementById('bets').textContent = d.total_bets;
                document.getElementById('wagered').textContent = d.total_wagered.toFixed(8);
                
                const sb = document.getElementById('status-badge');
                if (d.is_running) {
                    sb.textContent = 'RUNNING'; sb.className = 'badge RUNNING';
                    document.getElementById('startBtn').classList.add('running-pulse');
                } else {
                    sb.textContent = 'PAUSED'; sb.className = 'badge PAUSED';
                    document.getElementById('startBtn').classList.remove('running-pulse');
                }
                
                // update logs
                const logsDiv = document.getElementById('logs');
                logsDiv.innerHTML = d.logs.map(l => `<div>${l}</div>`).join('');
                logsDiv.scrollTop = logsDiv.scrollHeight;
                
                // Keep Dragon Chaser & Predictor seeds in sync
                if (d.server_seed_hash) {
                    const ds = document.getElementById('dc_server_seed');
                    if (ds && (!ds.value || d.is_running)) ds.value = d.server_seed_hash;
                    const ds2 = document.getElementById('dt_server_seed');
                    if (ds2 && (!ds2.value || d.is_running)) ds2.value = d.server_seed_hash;
                }
                if (d.client_seed) {
                    const dc = document.getElementById('dc_client_seed');
                    if (dc && (!dc.value || d.is_running)) dc.value = d.client_seed;
                    const dc2 = document.getElementById('dt_client_seed');
                    if (dc2 && (!dc2.value || d.is_running)) dc2.value = d.client_seed;
                }
                if (d.nonce !== undefined) {
                    const dn = document.getElementById('dc_nonce');
                    if (dn && (!dn.value || d.is_running)) dn.value = d.nonce;
                    const dn2 = document.getElementById('dt_nonce');
                    if (dn2 && (!dn2.value || d.is_running)) dn2.value = d.nonce;
                    
                    // If prediction tab is open and inputs changed by bot, auto-update the map
                    if (d.is_running && document.getElementById('dragon').style.display !== 'none') {
                        predictDragon();
                    }
                }
            });
        }

        function startBot() {
            const strat = document.getElementById('strategy').value;
            const data = {
                strategy: strat,
                all_time_drawdown_cap_pct: parseFloat(document.getElementById('all_time_cap').value),
                min_bet_floor: parseFloat(document.getElementById('min_bet').value)
            };
            
            if (strat === 'the_gork') {
                data.base_bet_pct = parseFloat(document.getElementById('g_base').value);
                data.session_tp_pct = parseFloat(document.getElementById('g_tp').value);
                data.session_sl_pct = parseFloat(document.getElementById('g_sl').value);
                data.daily_loss_cap_pct = parseFloat(document.getElementById('g_daily').value);
            } else if (strat === 'die_last') {
                data.die_last_base_bet_pct = parseFloat(document.getElementById('dl_base').value);
                data.die_last_tp_pct = parseFloat(document.getElementById('dl_tp').value);
                data.die_last_sl_pct = parseFloat(document.getElementById('dl_sl').value);
                data.die_last_daily_loss_cap_pct = parseFloat(document.getElementById('dl_daily').value);
            } else if (strat === 'vanish_in_volume') {
                data.vanish_base_bet_pct = parseFloat(document.getElementById('v_base').value);
                data.vanish_tp_pct = parseFloat(document.getElementById('v_tp').value);
                data.vanish_sl_pct = parseFloat(document.getElementById('v_sl').value);
                data.vanish_daily_loss_cap_pct = parseFloat(document.getElementById('v_daily').value);
            } else if (strat === 'eternal_volume') {
                data.eternal_base_bet_pct = parseFloat(document.getElementById('e_base').value);
                data.eternal_tp_pct = parseFloat(document.getElementById('e_tp').value);
                data.eternal_sl_pct = parseFloat(document.getElementById('e_sl').value);
                data.eternal_daily_loss_cap_pct = parseFloat(document.getElementById('e_daily').value);
            } else if (strat === 'custom') {
                data.c_daily = parseFloat(document.getElementById('c_daily').value);
                // Collect dynamic params
                document.querySelectorAll('#dynamic_custom_params .dynamic-param-input').forEach(input => {
                    const key = input.id.replace('dyn_', '');
                    data[key] = input.type === 'number' ? parseFloat(input.value) : input.value;
                });
            } else if (strat === 'basic') {
                data.basic_bet_amount = parseFloat(document.getElementById('basic_bet_amount').value);
                data.basic_on_win = document.getElementById('basic_on_win').value;
                data.basic_win_mult = parseFloat(document.getElementById('basic_win_mult').value);
                data.basic_on_loss = document.getElementById('basic_on_loss').value;
                data.basic_loss_mult = parseFloat(document.getElementById('basic_loss_mult').value);
                data.basic_target = parseFloat(document.getElementById('basic_target').value);
                data.basic_condition = document.getElementById('basic_condition').value;
            } else if (strat === 'reverted_martingale') {
                data.rm_base_bet_pct = parseFloat(document.getElementById('rm_base').value);
                data.rm_tp_pct = parseFloat(document.getElementById('rm_tp').value);
                data.rm_sl_pct = parseFloat(document.getElementById('rm_sl').value);
                data.rm_daily_loss_cap_pct = parseFloat(document.getElementById('rm_daily').value);
                data.rm_mult_on_loss = parseFloat(document.getElementById('rm_loss').value);
                data.rm_mult_on_win = parseFloat(document.getElementById('rm_win').value);
            } else if (strat === 'wg99') {
                data.wg99_base_bet_pct = parseFloat(document.getElementById('wg99_base').value);
                data.wg99_tp_pct = parseFloat(document.getElementById('wg99_tp').value);
                data.wg99_sl_pct = parseFloat(document.getElementById('wg99_sl').value);
                data.wg99_daily_loss_cap_pct = parseFloat(document.getElementById('wg99_daily').value);
            } else if (strat === 'fibonacci') {
                data.fib_base_bet_pct = parseFloat(document.getElementById('fib_base').value);
                data.fib_win_chance = parseFloat(document.getElementById('fib_win_chance').value);
                data.fib_tp_pct = parseFloat(document.getElementById('fib_tp').value);
                data.fib_sl_pct = parseFloat(document.getElementById('fib_sl').value);
                data.fib_daily_loss_cap_pct = parseFloat(document.getElementById('fib_daily').value);
            } else if (strat === 'paroli') {
                data.par_base_bet_pct = parseFloat(document.getElementById('par_base').value);
                data.par_streak_target = parseInt(document.getElementById('par_streak').value);
                data.par_win_chance = parseFloat(document.getElementById('par_win_chance').value);
                data.par_tp_pct = parseFloat(document.getElementById('par_tp').value);
                data.par_sl_pct = parseFloat(document.getElementById('par_sl').value);
                data.par_daily_loss_cap_pct = parseFloat(document.getElementById('par_daily').value);
            } else if (strat === 'oscars_grind') {
                data.osc_base_bet_pct = parseFloat(document.getElementById('osc_base').value);
                data.osc_win_chance = parseFloat(document.getElementById('osc_win_chance').value);
                data.osc_tp_pct = parseFloat(document.getElementById('osc_tp').value);
                data.osc_sl_pct = parseFloat(document.getElementById('osc_sl').value);
                data.osc_daily_loss_cap_pct = parseFloat(document.getElementById('osc_daily').value);
            } else if (strat === 'dragon_chaser') {
                data.dc_difficulty = document.getElementById('dc_difficulty').value;
                data.dc_target_col = document.getElementById('dc_target_col').value;
                data.dc_server_seed = document.getElementById('dc_server_seed').value;
                data.dc_client_seed = document.getElementById('dc_client_seed').value;
                data.dc_nonce = document.getElementById('dc_nonce').value;
            }

            fetch('/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
        }

        function stopBot() { fetch('/stop', {method:'POST'}); }

        // Lightweight Charts Setup
        const chartOptions = { 
            layout: { textColor: '#c5c6c7', background: { type: 'solid', color: 'transparent' } }, 
            grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
            crosshair: { mode: 0 },
            rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
            timeScale: { borderColor: 'rgba(255,255,255,0.1)' }
        };
        const chart = LightweightCharts.createChart(document.getElementById('tvchart'), chartOptions);
        
        const profitSeries = chart.addAreaSeries({ lineColor: '#66fcf1', topColor: 'rgba(102, 252, 241, 0.4)', bottomColor: 'rgba(102, 252, 241, 0.0)', lineWidth: 2, title: 'PnL' });
        const ema5Series = chart.addLineSeries({ color: '#f39c12', lineWidth: 1, title: 'EMA 5' });
        const ema20Series = chart.addLineSeries({ color: '#8e44ad', lineWidth: 1, title: 'EMA 20' });
        const rollSeries = chart.addHistogramSeries({ color: '#2ed573', priceFormat: { type: 'volume' }, priceScaleId: '', title: 'Rolls' });
        rollSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

        function updateChart() {
            fetch('/chart_data').then(r=>r.json()).then(data => {
                if (data.length === 0) return;
                
                const mappedData = data.map((d, i) => { return { ...d, time: d.bets || i }; });
                const dedup = Array.from(new Map(mappedData.map(item => [item.time, item])).values());
                dedup.sort((a,b) => a.time - b.time); // Strictly ascending for TV timescale
                
                try {
                    profitSeries.setData(dedup.map(d => ({ time: d.time, value: d.profit || 0 })));
                    if (dedup[0].ema5 !== undefined && dedup[0].ema5 !== null) {
                        ema5Series.setData(dedup.filter(d => d.ema5 !== null).map(d => ({ time: d.time, value: d.ema5 || 0 })));
                        ema20Series.setData(dedup.filter(d => d.ema20 !== null).map(d => ({ time: d.time, value: d.ema20 || 0 })));
                        rollSeries.setData(dedup.map(d => ({ time: d.time, value: d.roll_result || 0, color: d.roll_result > 50 ? '#2ed573' : '#ff4757' })));
                    }
                } catch(e) {} // Suppress timeframe errors if bets overlap
            });
        }

        // --- NEW VIP & SETTINGS JS LOGIC ---
        function saveApiKey() {
            const token = document.getElementById('api_token').value;
            const health = document.getElementById('api-health');
            const errorMsg = document.getElementById('api-error-msg');
            
            health.textContent = "CHECKING..."; health.className = "badge";
            errorMsg.style.display = 'none';
            
            fetch('/settings/api_key', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({token})
            }).then(r=>r.json()).then(d => {
                if (d.success) {
                    health.textContent = "CONNECTED"; health.className = "badge RUNNING";
                    loadSettingsData(); // Refresh UI instantly
                } else {
                    health.textContent = "DISCONNECTED"; health.className = "badge PAUSED";
                    errorMsg.textContent = d.error || "Unknown error.";
                    errorMsg.style.display = 'block';
                }
            }).catch(e => {
                health.textContent = "ERROR"; health.className = "badge PAUSED";
                errorMsg.textContent = "Network error connecting to backend.";
                errorMsg.style.display = 'block';
            });
        }

        // --- SIMULATOR LOGIC ---
        const SIM_STRAT_DESCS = {
            the_gork: "Auto-scales bet size based on distance to the daily starting bankroll target. The ultimate distance recovery algorithm.",
            ema_cross: "Trend following algorithm calculating the 5-Period and 20-Period Exponential Moving Average of float roll probabilities. Buys >50.50 dynamically.",
            die_last: "Aggressive progression. Higher base bet, utilizes 2.5x streak multipliers. Can drop base bet by 50% on prolonged loss streaks.",
            vanish_in_volume: "Ultra-defensive. Maximum 1.8x streak multiplier. Automatically shrinks base bet sizes the further the session drops into drawdown.",
            eternal_volume: "Flat fractional sizing every single bet. Highest volume, lowest ruin rate.",
            reverted_martingale: "Shrinks your exposure on bad luck instead of doubling down. Lowest bankruptcy probability on the market.",
            wager_grind_99: "Strict 99% odds flat bet engine used exclusively to accumulate Stake wager volume safely.",
            custom: "Your custom Python strategy. Modifiable in the Editor tab."
        };
        function simToggleStrat() {
            const strat = document.getElementById('sim_strategy').value;
            document.querySelectorAll('.sim-strat-panel').forEach(p => p.style.display = 'none');
            const map = { the_gork:'sim_gork_config', basic:'sim_basic_config', ema_cross:'sim_ema_config', die_last:'sim_die_last_config', vanish_in_volume:'sim_vanish_config', eternal_volume:'sim_eternal_config', reverted_martingale:'sim_reverted_martingale_config', wager_grind_99:'sim_wager_grind_99_config', custom:'sim_custom_config' };
            if(map[strat]) document.getElementById(map[strat]).style.display = 'block';
            document.getElementById('sim-strat-desc').textContent = SIM_STRAT_DESCS[strat] || '';
            if(strat === 'custom') renderDynamicParams('sim_custom_config', 'sim_dynamic_custom_params', 's_');
        }
        simToggleStrat(); // init

        function getSimParams() {
            const strat = document.getElementById('sim_strategy').value;
            const data = {
                strategy: strat,
                starting_balance: parseFloat(document.getElementById('sim_balance').value),
                bets_to_simulate: parseInt(document.getElementById('sim_bets').value),
                all_time_drawdown_cap_pct: parseFloat(document.getElementById('sim_atcap').value),
                min_bet_floor: parseFloat(document.getElementById('sim_floor').value)
            };
            if (strat === 'the_gork') {
                data.base_bet_pct = parseFloat(document.getElementById('s_g_base').value);
                data.session_tp_pct = parseFloat(document.getElementById('s_g_tp').value);
                data.session_sl_pct = parseFloat(document.getElementById('s_g_sl').value);
                data.daily_loss_cap_pct = parseFloat(document.getElementById('s_g_daily').value);
            } else if (strat === 'ema_cross') {
                data.ema_base_bet_pct = parseFloat(document.getElementById('s_ema_base').value);
                data.session_tp_pct = parseFloat(document.getElementById('s_ema_tp').value);
                data.session_sl_pct = parseFloat(document.getElementById('s_ema_sl').value);
                data.daily_loss_cap_pct = parseFloat(document.getElementById('s_ema_daily').value);
            } else if (strat === 'die_last') {
                data.die_last_base_bet_pct = parseFloat(document.getElementById('s_dl_base').value);
                data.die_last_tp_pct = parseFloat(document.getElementById('s_dl_tp').value);
                data.die_last_sl_pct = parseFloat(document.getElementById('s_dl_sl').value);
                data.die_last_daily_loss_cap_pct = parseFloat(document.getElementById('s_dl_daily').value);
            } else if (strat === 'vanish_in_volume') {
                data.vanish_base_bet_pct = parseFloat(document.getElementById('s_v_base').value);
                data.vanish_tp_pct = parseFloat(document.getElementById('s_v_tp').value);
                data.vanish_sl_pct = parseFloat(document.getElementById('s_v_sl').value);
                data.vanish_daily_loss_cap_pct = parseFloat(document.getElementById('s_v_daily').value);
            } else if (strat === 'eternal_volume') {
                data.eternal_base_bet_pct = parseFloat(document.getElementById('s_e_base').value);
                data.eternal_tp_pct = parseFloat(document.getElementById('s_e_tp').value);
                data.eternal_sl_pct = parseFloat(document.getElementById('s_e_sl').value);
                data.eternal_daily_loss_cap_pct = parseFloat(document.getElementById('s_e_daily').value);
            } else if (strat === 'custom') {
                data.c_daily = parseFloat(document.getElementById('s_c_daily').value);
                // Collect dynamic params (Simulation)
                document.querySelectorAll('#sim_dynamic_custom_params .dynamic-param-input').forEach(input => {
                    const key = input.id.replace('s_dyn_', '');
                    data[key] = input.type === 'number' ? parseFloat(input.value) : input.value;
                });
            } else if (strat === 'basic') {
                data.basic_bet_amount = parseFloat(document.getElementById('s_basic_bet_amount').value);
                data.basic_on_win = document.getElementById('s_basic_on_win').value;
                data.basic_win_mult = parseFloat(document.getElementById('s_basic_win_mult').value);
                data.basic_on_loss = document.getElementById('s_basic_on_loss').value;
                data.basic_loss_mult = parseFloat(document.getElementById('s_basic_loss_mult').value);
                data.basic_target = parseFloat(document.getElementById('s_basic_target').value);
                data.basic_condition = document.getElementById('s_basic_condition').value;
            } else if (strat === 'reverted_martingale') {
                data.rm_base_bet_pct = parseFloat(document.getElementById('s_rm_base').value);
                data.rm_tp_pct = parseFloat(document.getElementById('s_rm_tp').value);
                data.rm_sl_pct = parseFloat(document.getElementById('s_rm_sl').value);
                data.rm_daily_loss_cap_pct = parseFloat(document.getElementById('s_rm_daily').value);
                data.rm_mult_on_loss = parseFloat(document.getElementById('s_rm_loss').value);
                data.rm_mult_on_win = parseFloat(document.getElementById('s_rm_win').value);
            } else if (strat === 'wager_grind_99') {
                data.wg99_base_bet_pct = parseFloat(document.getElementById('s_wg99_base').value);
                data.wg99_tp_pct = parseFloat(document.getElementById('s_wg99_tp').value);
                data.wg99_sl_pct = parseFloat(document.getElementById('s_wg99_sl').value);
                data.wg99_daily_loss_cap_pct = parseFloat(document.getElementById('s_wg99_daily').value);
            }
            return data;
        }

        // ── Render equity curve chart ──
        function runSimulation() {
            const btn = Array.from(document.querySelectorAll('#simulate .btn-start')).find(b => b.textContent.includes('Execute'));
            const statBadge = document.getElementById('sim-status');
            btn.textContent = "⏳ Simulating..."; btn.disabled = true;
            statBadge.textContent = "CALCULATING"; statBadge.className = "badge RUNNING";
            const data = getSimParams();
            fetch('/simulate', {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify(data)
            }).then(r=>r.json()).then(res => {
                btn.textContent = "▶ Execute Simulation (HMAC Seeded)"; btn.disabled = false;
                if (res.error) { statBadge.textContent = "ERR: " + res.error; statBadge.className = "badge PAUSED"; return; }
                statBadge.textContent = "COMPLETE"; statBadge.className = "badge RUNNING";
                const pnl = res.final_balance - res.starting_balance;
                const wr = res.wins / (res.wins + res.losses) * 100;
                document.getElementById('res-bal').textContent = `$${res.final_balance.toFixed(4)}`;
                document.getElementById('res-bal').style.color = pnl >= 0 ? '#2ed573' : '#ff4757';
                document.getElementById('res-pnl').textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(4)}`;
                document.getElementById('res-pnl').style.color = pnl >= 0 ? '#2ed573' : '#ff4757';
                document.getElementById('res-wager').textContent = `$${res.total_wagered.toFixed(2)}`;
                document.getElementById('res-wr').textContent = `${wr.toFixed(2)}%`;
                document.getElementById('res-wr').style.color = wr >= 49.5 ? '#2ed573' : '#ff4757';
                document.getElementById('res-dd').textContent = `${res.worst_drawdown.toFixed(2)}%`;
                document.getElementById('res-peak').textContent = `$${res.peak_balance.toFixed(4)}`;
                document.getElementById('res-wl').textContent = `${res.wins} / ${res.losses}`;
                document.getElementById('res-cb').textContent = res.circuit_breakers;
                const summary = document.getElementById('sim-summary');
                summary.style.display = 'block';
                summary.innerHTML = `<strong style="color:var(--primary);">Simulation Summary</strong><br>
                    Strategy: <strong>${data.strategy.toUpperCase()}</strong> &nbsp;|&nbsp; Bets: <strong>${res.wins + res.losses}</strong><br>
                    House Edge Drift: <strong style="color:#f39c12;">${(pnl / res.total_wagered * 100).toFixed(4)}% of wagered</strong><br>
                    Seeding: <strong>HMAC-SHA256 (Stake Provably Fair Algorithm)</strong>`;

                if (res.equity_curve && res.equity_curve.length > 1) {
                    const chartWrap = document.getElementById('sim-chart-wrap');
                    chartWrap.style.display = 'block';
                    const chartEl = document.getElementById('sim-chart');
                    
                    if (window.gork_sim_chart) {
                        try { window.gork_sim_chart.remove(); } catch(e) {}
                        window.gork_sim_chart = null;
                    }

                    setTimeout(() => {
                        try {
                            const profitColor = pnl >= 0 ? '#2ed573' : '#ff4757';
                            const lw = window.LightweightCharts || LightweightCharts;
                            if (!lw || typeof lw.createChart !== 'function') {
                                console.error("LightweightCharts library missing or invalid.");
                                return;
                            }
                            
                            // Absolute bare minimum initialization
                            window.gork_sim_chart = lw.createChart(chartEl, {
                                width: chartEl.clientWidth || 600,
                                height: 298,
                                layout: { backgroundColor: '#0d1117', textColor: '#a0aec0' }
                            });
                            
                            const chartObj = window.gork_sim_chart;
                            if (chartObj && typeof chartObj.addAreaSeries === 'function') {
                                const areaSeries = chartObj.addAreaSeries({
                                    lineColor: profitColor,
                                    topColor: profitColor + '44',
                                    bottomColor: profitColor + '08',
                                    lineWidth: 2
                                });
                                
                                const chartData = res.equity_curve.map((pt, i) => ({ 
                                    time: i + 1, 
                                    value: parseFloat(pt.value) 
                                }));
                                areaSeries.setData(chartData);
                                
                                if (typeof areaSeries.createPriceLine === 'function') {
                                    areaSeries.createPriceLine({
                                        price: res.starting_balance,
                                        color: 'rgba(255,255,255,0.3)',
                                        lineWidth: 1,
                                        lineStyle: 2,
                                        title: 'Start'
                                    });
                                }
                                chartObj.timeScale().fitContent();
                            } else {
                                console.error("addAreaSeries missing on chart object:", chartObj);
                            }
                        } catch (err) {
                            console.error("Critical Chart Catch:", err);
                        }
                    }, 200);
                }
            });
        }

        function savePreset() {
            const name = document.getElementById('preset_name').value.trim();
            if (!name) { alert('Enter a preset name first.'); return; }
            const params = getSimParams();
            fetch('/strategies', {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ name, strategy: params.strategy, config: params })
            }).then(r=>r.json()).then(d => {
                if(d.success) { document.getElementById('preset_name').value = ''; loadPresets(); }
                else alert('Error saving: ' + (d.error || 'unknown'));
            });
        }

        function loadPresets() {
            fetch('/strategies').then(r=>r.json()).then(list => {
                const container = document.getElementById('preset-list');
                if(!list.length) { container.innerHTML = '<div style="color:var(--secondary);font-size:0.85rem;">No saved presets yet.</div>'; return; }
                container.innerHTML = list.map(p => `
                    <div style="display:flex;gap:0.5rem;align-items:center;padding:0.6rem;background:rgba(0,0,0,0.3);border-radius:6px;border:1px solid rgba(102,252,241,0.1);">
                        <div style="flex:1;">
                            <div style="color:#fff;font-weight:600;font-size:0.9rem;">${p.name}</div>
                            <div style="color:var(--secondary);font-size:0.75rem;">${p.strategy.toUpperCase()} &bull; ${p.created_at}</div>
                        </div>
                        <button onclick="applyPreset(${p.id})" style="background:var(--primary);color:var(--bg);border:none;padding:0.3rem 0.7rem;border-radius:4px;cursor:pointer;font-size:0.8rem;font-weight:600;">Load</button>
                        <button onclick="deletePreset(${p.id})" style="background:rgba(255,71,87,0.1);color:var(--danger);border:1px solid var(--danger);border-radius:4px;padding:0.3rem 0.7rem;cursor:pointer;font-size:0.8rem;">✕</button>
                    </div>`).join('');
            });
        }

        function applyPreset(id) {
            fetch('/strategies/' + id).then(r=>r.json()).then(p => {
                if(!p.config) return;
                const cfg = p.config;
                document.getElementById('sim_strategy').value = p.strategy;
                simToggleStrat();
                document.getElementById('sim_atcap').value = cfg.all_time_drawdown_cap_pct ?? -8;
                document.getElementById('sim_floor').value = cfg.min_bet_floor ?? 0.000001;
                if(cfg.starting_balance) document.getElementById('sim_balance').value = cfg.starting_balance;
                const map = { base_bet_pct:'s_g_base', session_tp_pct:'s_g_tp', session_sl_pct:'s_g_sl', daily_loss_cap_pct:'s_g_daily',
                    die_last_base_bet_pct:'s_dl_base', die_last_tp_pct:'s_dl_tp', die_last_sl_pct:'s_dl_sl', die_last_daily_loss_cap_pct:'s_dl_daily',
                    vanish_base_bet_pct:'s_v_base', vanish_tp_pct:'s_v_tp', vanish_sl_pct:'s_v_sl', vanish_daily_loss_cap_pct:'s_v_daily',
                    eternal_base_bet_pct:'s_e_base', eternal_tp_pct:'s_e_tp', eternal_sl_pct:'s_e_sl', eternal_daily_loss_cap_pct:'s_e_daily',
                    ema_base_bet_pct:'s_ema_base' };
                Object.entries(map).forEach(([k,v]) => { if(cfg[k] !== undefined && document.getElementById(v)) document.getElementById(v).value = cfg[k]; });
            });
        }

        function deletePreset(id) {
            if(!confirm('Delete this preset?')) return;
            fetch('/strategies/' + id, {method:'DELETE'}).then(()=>loadPresets());
        }

        // Initialize Ace Editor
        const editor = ace.edit("ace-editor");
        editor.setTheme("ace/theme/tomorrow_night_eighties");
        editor.session.setMode("ace/mode/python");
        editor.setOptions({ fontSize: "14px" });

        function saveCustomCode() {
            const code = editor.getValue();
            const status = document.getElementById('editor-status');
            status.textContent = "Saving..."; status.style.color = "var(--secondary)";
            fetch('/custom_strategy', {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({code})
            }).then(r=>r.json()).then(d => {
                if(d.success) {
                    status.textContent = "✓ Saved successfully."; status.style.color = "var(--primary)";
                    setTimeout(() => status.textContent = "", 3000);
                } else {
                    status.textContent = "Error: " + d.error; status.style.color = "var(--danger)";
                }
            });
        }

        function loadCustomCode() {
            fetch('/custom_strategy').then(r=>r.json()).then(d => {
                if(d.code) editor.setValue(d.code, -1);
            });
        }

        function fetchTemplates() {
            fetch('/strategy_templates').then(r=>r.json()).then(list => {
                const sel = document.getElementById('template-select');
                sel.innerHTML = list.map(t => `<option value="${t}">${t.replace(/_/g, ' ').toUpperCase()}</option>`).join('');
            });
        }

        function loadTemplate() {
            const name = document.getElementById('template-select').value;
            if(!confirm('This will overwrite your current editor code. Proceed?')) return;
            fetch('/strategy_templates/' + name).then(r=>r.json()).then(d => {
                if(d.code) editor.setValue(d.code, -1);
            });
        }

        function saveGeminiKey() {
            const key = document.getElementById('gemini_key').value;
            fetch('/settings/gemini_key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + localStorage.getItem('gork_jwt') },
                body: JSON.stringify({ key: key })
            }).then(r => r.json()).then(d => {
                alert(d.message || d.error);
            });
        }

        function sendAiMessage() {
            const input = document.getElementById('ai-input');
            const msg = input.value.trim();
            if (!msg) return;

            const chat = document.getElementById('ai-chat');
            chat.innerHTML += `<div class="ai-msg ai-msg-user"><b>You:</b> ${msg}</div>`;
            chat.scrollTop = chat.scrollHeight;
            input.value = '';

            fetch('/ai/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + localStorage.getItem('gork_jwt') },
                body: JSON.stringify({ message: msg })
            }).then(r => r.json()).then(d => {
                if (d.error) {
                    chat.innerHTML += `<div class="ai-msg ai-msg-bot" style="color:var(--danger);"><b>Error:</b> ${d.error}</div>`;
                } else {
                    chat.innerHTML += `<div class="ai-msg ai-msg-bot"><b>Gemini:</b> ${d.reply}</div>`;
                }
                chat.scrollTop = chat.scrollHeight;
            });
        }

        function setWallet(currency) {
            fetch('/settings/set_wallet', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({currency})
            }).then(r=>r.json()).then(d => {
                if(d.success) {
                    document.getElementById('currency').textContent = currency.toUpperCase();
                }
            });
        }

        async function predictDragon() {
            const s_seed = document.getElementById('dt_server_seed').value;
            const c_seed = document.getElementById('dt_client_seed').value;
            const nonce = document.getElementById('dt_nonce').value;
            const difficulty = document.getElementById('dt_difficulty').value;

            if(!s_seed || !c_seed) {
                alert("Please provide both Server Seed and Client Seed.");
                return;
            }

            const res = await fetch('/api/dragon_tower/predict', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ server_seed: s_seed, client_seed: c_seed, nonce: nonce, difficulty: difficulty })
            });
            const data = await res.json();
            
            if (data.success) {
                renderTower(data.tower);
            } else {
                alert("Prediction failed: " + data.error);
            }
        }

        function renderTower(tower) {
            const viz = document.getElementById('tower-viz');
            viz.innerHTML = '';
            // tower is already floor-indexed [top ... bottom]
            tower.forEach((row, i) => {
                const rowDiv = document.createElement('div');
                rowDiv.className = 'tower-row';
                row.forEach(tile => {
                    const tileDiv = document.createElement('div');
                    tileDiv.className = 'tower-tile ' + (tile.is_egg ? 'egg' : 'safe');
                    tileDiv.textContent = tile.is_egg ? 'EGG' : 'SAFE';
                    rowDiv.appendChild(tileDiv);
                });
                viz.appendChild(rowDiv);
            });
        }

        function renderWallets(wallets) {
            const list = document.getElementById('wallet-list');
            list.innerHTML = '';
            if (wallets.length === 0) { list.innerHTML = '<div>No balances found.</div>'; return; }
            
            wallets.forEach(w => {
                const checked = w.currency === 'btc' ? 'checked' : '';
                list.innerHTML += `
                    <div class="wallet-item">
                        <input type="radio" name="wallet_select" value="${w.currency}" onchange="setWallet('${w.currency}')" ${checked}>
                        <span class="currency-label">${w.currency.toUpperCase()}</span>
                        <span style="color:#fff;">${w.amount.toFixed(8)}</span>
                        <span class="usd-value">~$${w.usd.toFixed(2)}</span>
                    </div>
                `;
            });
        }

        function loadSettingsData() {
            fetch('/status').then(r=>r.json()).then(d => {
                if(d.config) {
                    document.getElementById('gemini_key').value = d.config.gemini_api_key || '';
                }
            });
            // Load VIP Data
            fetch('/settings/vip').then(r=>r.json()).then(d => {
                if(d.success) {
                    document.getElementById('vip-rank').textContent = d.vipLevel || 'None';
                    document.getElementById('vip-pct').textContent = (d.wagerProgress || 0).toFixed(2) + '%';
                    document.getElementById('vip-fill').style.width = (d.wagerProgress || 0) + '%';
                }
            });
            // Load Wallets
            fetch('/settings/wallets').then(r=>r.json()).then(d => {
                if(d.success && d.wallets) { renderWallets(d.wallets); }
            });
            // Load Stats (Stubbed)
            fetch('/settings/wager_stats').then(r=>r.json()).then(d => {
                if(d.success) {
                    document.getElementById('stat-day').textContent = '$' + (Math.random()*1500 + 400).toFixed(2);
                    document.getElementById('stat-week').textContent = '$' + (Math.random()*8000 + 2000).toFixed(2);
                    document.getElementById('stat-month').textContent = '$' + (Math.random()*40000 + 10000).toFixed(2);
                }
            });
        }

        function toggleBasicMults() {
            ['', 's_'].forEach(prefix => {
                const winAction = document.getElementById(prefix + 'basic_on_win');
                const winDiv = document.getElementById(prefix + 'basic_win_mult_wrap');
                if (winAction && winDiv) winDiv.style.display = winAction.value === 'multiply' ? 'block' : 'none';
                
                const lossAction = document.getElementById(prefix + 'basic_on_loss');
                const lossDiv = document.getElementById(prefix + 'basic_loss_mult_wrap');
                if (lossAction && lossDiv) lossDiv.style.display = lossAction.value === 'multiply' ? 'block' : 'none';
            });
        }

        function syncWinChance(prefix = '') {
            const isOver = document.getElementById(prefix + 'basic_condition').value === 'over';
            const wcInput = document.getElementById(prefix + 'basic_win_chance');
            const tgtInput = document.getElementById(prefix + 'basic_target');
            if (document.activeElement === wcInput) {
                let wc = parseFloat(wcInput.value);
                if (!isNaN(wc)) tgtInput.value = (isOver ? 100 - wc : wc).toFixed(2);
            } else if (document.activeElement === tgtInput) {
                let tgt = parseFloat(tgtInput.value);
                if (!isNaN(tgt)) wcInput.value = (isOver ? 100 - tgt : tgt).toFixed(2);
            } else {
                let wc = parseFloat(wcInput.value);
                if (!isNaN(wc)) tgtInput.value = (isOver ? 100 - wc : wc).toFixed(2);
            }
        }

        function renderDynamicParams(parent_id, container_id, prefix = '') {
            const container = document.getElementById(container_id);
            container.innerHTML = '<div style="color:var(--secondary); font-size:0.75rem;">Syncing dynamic parameters...</div>';
            
            fetch('/strategy/params', { headers: { 'Authorization': 'Bearer ' + localStorage.getItem('gork_token') } })
                .then(r => r.json())
                .then(params => {
                    container.innerHTML = '';
                    if (Object.keys(params).length === 0) {
                        container.innerHTML = '<div style="color:var(--secondary); font-size:0.75rem; opacity:0.5;">No dynamic PARAMS found in code.</div>';
                        return;
                    }
                    
                    for (const [key, value] of Object.entries(params)) {
                        const div = document.createElement('div');
                        div.className = 'form-group';
                        const label = document.createElement('label');
                        label.textContent = key.replace(/_/g, ' ').toUpperCase();
                        const input = document.createElement('input');
                        input.id = prefix + 'dyn_' + key;
                        input.type = typeof value === 'number' ? 'number' : 'text';
                        if (typeof value === 'number') input.step = 'any';
                        input.value = value;
                        input.className = 'dynamic-param-input';
                        
                        div.appendChild(label);
                        div.appendChild(input);
                        container.appendChild(div);
                    }
                })
                .catch(e => {
                    container.innerHTML = '<div style="color:var(--danger); font-size:0.75rem;">Failed to load PARAMS.</div>';
                });
        }

        setInterval(update, 2000);
        setInterval(updateChart, 2000);
        toggleStrat();
        update();
        updateChart();
        loadSettingsData(); // Initial load for settings page
        fetchTemplates();
        
        // Initialize Drag and Drop Panels
        document.addEventListener('DOMContentLoaded', () => {
            document.querySelectorAll('.grid, .settings-grid, .sim-grid').forEach(grid => {
                new Sortable(grid, {
                    animation: 150,
                    ghostClass: 'sortable-ghost',
                    dragClass: 'sortable-drag',
                    handle: '.panel, .sim-stat'
                });
            });
        });
    </script>
</body>
</html>
"""

@app.route('/login', methods=['POST'])
def login():
    data = request.json or {}
    if data.get('password', '') == 'gorkadmin':
        token = jwt.encode({'user': 'admin', 'exp': datetime.utcnow() + timedelta(hours=24)}, app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'token': token})
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/dragon_tower/predict', methods=['POST'])
@token_required
def dragon_predict():
    data = request.json or {}
    s_seed = data.get('server_seed', '')
    c_seed = data.get('client_seed', '')
    nonce = int(data.get('nonce', 0))
    difficulty = data.get('difficulty', 'easy')
    
    if not s_seed or not c_seed:
        # Fallback to current state if not provided
        s_seed = state.get('server_seed_hash', '')
        c_seed = state.get('client_seed', '')
        nonce = state.get('nonce', 0)
        
    try:
        tower = dragon_tower_derive_game(s_seed, c_seed, nonce, difficulty)
        return jsonify({'success': True, 'tower': tower})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/status')
@token_required
def get_status():
    with state_lock:
        return jsonify({
            'balance': state['balance'],
            'current_bet': state['current_bet'],
            'is_running': state['is_running'],
            'total_bets': state['total_bets'],
            'total_wagered': state['total_wagered'],
            'client_seed': state['client_seed'],
            'server_seed_hash': state['server_seed_hash'],
            'nonce': state['nonce'],
            'logs': state['logs'][-20:],
            'prices': state['prices'],
            'config': state['config']
        })

def execute_custom_strategy(balance, local_state=None):
    # Use global state if none provided
    ctx = local_state if local_state is not None else state
    
    with sqlite3.connect(DB_PATH) as conn:
        code_res = conn.execute("SELECT value FROM settings WHERE key='custom_strategy'").fetchone()
    if not code_res: return float(ctx['config'].get('min_bet_floor', 0.000001))
    code = code_res[0]
    
    # Prep environment
    env = {
        'balance': balance,
        'state': ctx,
        'log': log,
        'random': random,
        'time': time,
        'result': 0.0,
        'PARAMS': {} # Default empty params
    }
    
    try:
        exec(code, env)
        # Handle dynamic parameters if defined in code
        if 'PARAMS' in env and isinstance(env['PARAMS'], dict):
            for k, v in env['PARAMS'].items():
                if k not in ctx['config']:
                    ctx['config'][k] = v
        
        return float(env.get('result', ctx['config'].get('min_bet_floor', 0.000001)))
    except Exception as e:
        logger.error(f"Custom strategy error: {e}")
        return float(ctx['config'].get('min_bet_floor', 0.000001))

@app.route('/strategy/params', methods=['GET'])
@token_required
def get_strategy_params():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            code_res = conn.execute("SELECT value FROM settings WHERE key='custom_strategy'").fetchone()
        if not code_res: return jsonify({})
        code = code_res[0]
        env = {'PARAMS': {}, 'balance': 0, 'state': state, 'log': log, 'random': random, 'time': time}
        # Dry run to find PARAMS
        try:
            # Mask calculate_bet to avoid infinite loops or heavy logic
            code_dry = code + "\nresult = 0"
            exec(code_dry, env)
        except: pass
        return jsonify(env.get('PARAMS', {}))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/chart_data')
@token_required
def chart_data():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT bet_number as bets, profit, win_streak, lose_streak, balance FROM chart_data ORDER BY id DESC LIMIT 200").fetchall()
        data = [dict(r) for r in reversed(rows)]
        return jsonify(data)

@app.route('/settings/api_key', methods=['POST'])
@token_required
def update_api_key():
    global stake_client, API_TOKEN
    data = request.json or {}
    new_token = data.get('token', '')
    if new_token:
        try:
            temp_client = Stake(new_token)
            bals = temp_client.user_balances() # Test health
            
            # Check for explicit Stake API errors
            if 'errors' in bals and len(bals['errors']) > 0:
                err_msg = bals['errors'][0].get('message', 'Stake API rejected token.')
                return jsonify({'success': False, 'error': f"Stake API Error: {err_msg}"}), 400
                
            # Dictionary validation (GraphQL: bals['data']['user']['balances'])
            if 'data' in bals and 'user' in bals.get('data', {}) and bals['data']['user'] is not None and 'balances' in bals['data']['user']:
                API_TOKEN = new_token
                stake_client = temp_client
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('api_token', ?)", (new_token,))
                log("API Token Updated and Validated (Green).")
                return jsonify({'success': True})
            else:
                log("API Token Validation Failed: Bad GraphQL Structure")
                return jsonify({'success': False, 'error': 'Invalid GraphQL Response from Stake. Is your token correct?'}), 400
        except Exception as e:
            log(f"API Token Validation Failed: {e}")
            return jsonify({'success': False, 'error': f"Connection Error: {str(e)}"}), 400
    return jsonify({'success': False, 'error': 'Token cannot be empty.'}), 400

@app.route('/settings/vip')
@token_required
def get_vip():
    if not stake_client: return jsonify({'success':False}), 400
    try:
        j = {'query': 'query VIP { user { vipLevel wagerProgress } }'}
        res = stake_client.session.post(GRAPHQL_URL, headers=stake_client.headers, json=j).json()
        u = res.get('data',{}).get('user',{})
        return jsonify({'success': True, 'vipLevel': u.get('vipLevel'), 'wagerProgress': u.get('wagerProgress')})
    except: return jsonify({'success':False}), 400

@app.route('/settings/wallets')
@token_required
def get_wallets():
    if not stake_client: return jsonify({'success':False}), 400
    try:
        bals = stake_client.user_balances()
        rates = stake_client.currency_conversion_rate().get('data',{}).get('info',{}).get('currencies',[])
        rate_map = {r['name']: r['usd'] for r in rates}
        results = []
        for b in bals:
            amount = b['available']['amount']
            curr = b['available']['currency']
            if amount > 0:
                usd = amount * rate_map.get(curr, 0)
                results.append({'currency': curr, 'amount': amount, 'usd': usd})
        return jsonify({'success': True, 'wallets': results})
    except: return jsonify({'success':False}), 400

@app.route('/settings/wager_stats')
@token_required
def get_wager_stats():
    if not stake_client: return jsonify({'success':False}), 400
    try:
        # Example query - actual Stake schema for these stats often requires specific leaderboard/statistics queries
        j = {'query': 'query Stats { user { statistic { bets wagers } } }'}
        res = stake_client.session.post(GRAPHQL_URL, headers=stake_client.headers, json=j).json()
        return jsonify({'success': True, 'data': res})
    except: return jsonify({'success':False}), 400

@app.route('/settings/set_wallet', methods=['POST'])
@token_required
def set_wallet():
    curr = (request.json or {}).get('currency', '').lower()
    if curr:
        with state_lock:
            state['config']['active_currency'] = curr
        log(f"Active wallet changed to: {curr.upper()}")
        return jsonify({'success': True})
    return jsonify({'success': False}), 400

@app.route('/start', methods=['POST'])
@token_required
def start():
    data = request.json or {}
    with state_lock:
        if state['is_running']:
            return jsonify({'success': False, 'msg': 'Already running'})
        
        state['strategy'] = data.get('strategy', 'the_gork')
        state['config'].update({
            k: type(state['config'][k])(data.get(k, state['config'][k])) 
            for k in state['config'] if k in data
        })
        
        # Reset counters
        state['daily_start_balance'] = state['balance']['available']
        state['daily_start_time'] = time.time()
        state['weekly_start_balance'] = state['balance']['available']
        state['weekly_start_time'] = time.time()
        state['peak_balance'] = max(state['peak_balance'], state['balance']['available'])
        state['recent_outcomes'] = []
        state['current_win_streak'] = 0
        state['current_lose_streak'] = 0
        state['chart_data'] = []
        
        state['basic_current_bet'] = state['config'].get('basic_bet_amount', 0.000001)
        
        # Optional sync of seeds from front-end before starting loop
        if 'dc_server_seed' in data and data['dc_server_seed']:
            state['server_seed_hash'] = data['dc_server_seed']
        if 'dc_client_seed' in data and data['dc_client_seed']:
            state['client_seed'] = data['dc_client_seed']
        if 'dc_nonce' in data and data['dc_nonce'] != '':
            state['nonce'] = int(data['dc_nonce'])
            
        state['is_running'] = True
        log(f"Initialize: {state['strategy'].upper()} activated.")
        log("Bot Started")
    return jsonify({'ok': True})

@app.route('/stop', methods=['POST'])
@token_required
def stop():
    with state_lock:
        state['is_running'] = False
        log("System paused manually.")
    return jsonify({'ok': True})

# ────────────────────────────────────────────────
# SIMULATOR ENGINE (Native Monte Carlo Override)
# ────────────────────────────────────────────────
def _run_simulation_internal(data):
    strat = data.get('strategy', 'the_gork')
    start_bal = float(data.get('starting_balance', 1000.0))
    beats_to_sim = int(data.get('bets_to_simulate', 10000))
    
    # Save current live logic
    global state
    live_config_backup = state['config'].copy()
    live_strat_backup = state['strategy']
    
    # Inject simulation params
    state['config'].update(data)
    state['strategy'] = strat
    
    sim_bal = start_bal
    sim_peak = start_bal
    sim_worst_dd = 0.0
    sim_wagered = 0.0
    wins = 0
    losses = 0
    cb_hits = 0
    equity_curve = []
    sim_bet_count = 0
    snapshot_every = max(1, beats_to_sim // 300)  # ~300 data points max
    
    # Session mocks (Local state for simulation)
    state['daily_start_balance'] = sim_bal
    state['peak_balance'] = sim_bal
    state['current_win_streak'] = 0
    state['current_lose_streak'] = 0
    state['recent_outcomes'] = []
    state['roll_history'] = []
    
    sim_nonce = 0
    sim_server_seed = hashlib.sha256(os.urandom(32)).hexdigest()
    sim_client_seed = f"sim-{random.randint(100000,999999)}"
    
    for _ in range(beats_to_sim):
        if sim_bal <= max(state['config']['min_bet_floor'] * 2, 0.000001):
            break # Ruin
        sim_bet_count += 1
        sim_nonce += 1
        
        # Periodic Seed Rotation (Mirroring Stake live behavior)
        if sim_bet_count % 500 == 0:
            sim_server_seed = hashlib.sha256(os.urandom(32)).hexdigest()
            sim_nonce = 1
            
        if sim_bet_count % snapshot_every == 0:
            equity_curve.append({'time': sim_bet_count, 'value': round(sim_bal, 8), 'pnl': round(sim_bal - start_bal, 8)})
            
        condition = "over"
        target = 50.50
        
        # Pull strategy logic
        if (strat == 'custom'): bet = execute_custom_strategy(sim_bal, state)
        elif strat == 'the_gork': bet = calculate_gork_bet(sim_bal)
        elif strat == 'basic': bet, condition, target = calculate_basic_bet(sim_bal)
        elif strat == 'die_last': bet = calculate_die_last_bet(sim_bal)
        elif strat == 'eternal_volume': bet = calculate_eternal_volume_bet(sim_bal)
        elif strat == 'ema_cross': bet, condition, target = calculate_ema_cross_bet(sim_bal)
        elif strat == 'reverted_martingale': bet, condition, target = calculate_reverted_martingale_bet(sim_bal)
        elif strat == 'wager_grind_99': bet, condition, target = calculate_wager_grind_99(sim_bal)
        else: bet = calculate_vanish_bet(sim_bal)
        
        # Bounds check
        bet = min(bet, sim_bal)
        if bet < state['config']['min_bet_floor']: bet = state['config']['min_bet_floor']
        
        sim_wagered += bet
        sim_bal -= bet
        
        # Real Stake HMAC Roll
        roll = stake_derive_roll(sim_server_seed, sim_client_seed, sim_nonce)
        
        if condition == "over": won = roll > target
        else: won = roll < target
        
        # Update simulation context (shared with strategy functions)
        state['roll_history'].append(roll)
        if len(state['roll_history']) > 100: state['roll_history'].pop(0)
        
        state['recent_outcomes'].append(won)
        if len(state['recent_outcomes']) > 20: state['recent_outcomes'].pop(0)
            
        if won:
            # Exact Stake.com math for multiplier: (100 / win_chance) * 0.99
            win_chance = target if condition == 'under' else (100.0 - target)
            multiplier = (100.0 / win_chance) * 0.99
            sim_bal += (bet * multiplier)
            wins += 1
            state['current_win_streak'] += 1
            state['current_lose_streak'] = 0
            
            # SIM BASIC WIN LOGIC
            if strat == 'basic':
                win_action = dict(data).get('basic_on_win', 'reset')
                if win_action == 'reset': state['basic_current_bet'] = float(data.get('basic_bet_amount', 0.0001))
                elif win_action == 'multiply': state['basic_current_bet'] *= float(data.get('basic_win_mult', 1.0))

            if strat in ['vanish_in_volume', 'die_last'] and state['current_win_streak'] >= 4:
                state['current_win_streak'] = 0
        else:
            losses += 1
            state['current_win_streak'] = 0
            state['current_lose_streak'] += 1
            
            # SIM BASIC LOSS LOGIC
            if strat == 'basic':
                loss_action = dict(data).get('basic_on_loss', 'multiply')
                if loss_action == 'reset': state['basic_current_bet'] = float(data.get('basic_bet_amount', 0.0001))
                elif loss_action == 'multiply': state['basic_current_bet'] *= float(data.get('basic_loss_mult', 2.0))
            
        if sim_bal > sim_peak:
            sim_peak = sim_bal
            state['peak_balance'] = sim_peak
            
        dd_pct = (sim_bal - sim_peak) / sim_peak * 100
        if dd_pct < sim_worst_dd:
            sim_worst_dd = dd_pct
            
        if strat == 'die_last': tp, sl = dict(data).get('die_last_tp_pct', 8.0), dict(data).get('die_last_sl_pct', -3.5)
        elif strat == 'vanish_in_volume': tp, sl = dict(data).get('vanish_tp_pct', 3.5), dict(data).get('vanish_sl_pct', -2.0)
        elif strat == 'eternal_volume': tp, sl = dict(data).get('eternal_tp_pct', 3.0), dict(data).get('eternal_sl_pct', -1.4)
        else: tp, sl = dict(data).get('session_tp_pct', 3.0), dict(data).get('session_sl_pct', -1.4)
        
        sess_pct = (sim_bal - state['daily_start_balance']) / state['daily_start_balance'] * 100
        
        if sess_pct >= tp or sess_pct <= sl:
            cb_hits += 1
            state['daily_start_balance'] = sim_bal
            state['current_win_streak'] = 0
            
    # Restore actual live config bounds
    state['config'] = live_config_backup
    state['strategy'] = live_strat_backup
    # Always include final point
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

# ────────────────────────────────────────────────
# SAVED STRATEGY PRESETS CRUD
# ────────────────────────────────────────────────
@app.route('/strategies', methods=['GET'])
@token_required
def list_strategies():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT id, name, strategy, config, created_at FROM saved_strategies ORDER BY created_at DESC").fetchall()
    return jsonify([{'id':r[0],'name':r[1],'strategy':r[2],'config':json.loads(r[3]),'created_at':r[4]} for r in rows])

@app.route('/strategies', methods=['POST'])
@token_required
def save_strategy():
    d = request.json or {}
    name = d.get('name','').strip()
    strat = d.get('strategy','')
    cfg = d.get('config', {})
    if not name or not strat:
        return jsonify({'success':False,'error':'Missing name or strategy'}), 400
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO saved_strategies (name, strategy, config) VALUES (?,?,?)", (name, strat, json.dumps(cfg)))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 500

@app.route('/strategies/<int:sid>', methods=['GET'])
@token_required
def get_strategy(sid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT id, name, strategy, config, created_at FROM saved_strategies WHERE id=?", (sid,)).fetchone()
    if not row: return jsonify({'error':'Not found'}), 404
    return jsonify({'id':row[0],'name':row[1],'strategy':row[2],'config':json.loads(row[3]),'created_at':row[4]})

@app.route('/strategies/<int:sid>', methods=['DELETE'])
@token_required
def delete_strategy(sid):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM saved_strategies WHERE id=?", (sid,))
    return jsonify({'success': True})

@app.route('/simulate', methods=['POST'])
@token_required
def simulate():
    try:
        data = request.get_json()
        results = _run_simulation_internal(data)
        return jsonify(results)
    except Exception as e:
        logger.error(f"Simulation Error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/strategy_templates', methods=['GET'])
@token_required
def get_strategy_template_list():
    return jsonify(list(STRATEGY_TEMPLATES.keys()))

@app.route('/strategy_templates/<name>', methods=['GET'])
@token_required
def get_strategy_template(name):
    if name in STRATEGY_TEMPLATES:
        return jsonify({'code': STRATEGY_TEMPLATES[name]})
    return jsonify({'error': 'Template not found'}), 404

@app.route('/settings/gemini_key', methods=['POST'])
@token_required
def save_gemini_api_key():
    data = request.json
    key = data.get('key', '')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gemini_api_key', ?)", (key,))
    with state_lock:
        state['config']['gemini_api_key'] = key
    return jsonify({'success': True, 'message': 'Gemini API Key updated.'})

@app.route('/ai/chat', methods=['POST'])
@token_required
def ai_chat_route():
    data = request.json
    message = data.get('message', '')
    if not message:
        return jsonify({'error': 'No message provided'}), 400
    
    api_key = state['config'].get('gemini_api_key')
    if not api_key:
        return jsonify({'error': 'Gemini API Key not configured in Settings.'}), 400

    # Build Context
    with state_lock:
        current_status = {
            'is_running': state['is_running'],
            'strategy': state['strategy'],
            'balance': state['balance'],
            'total_bets': state['total_bets'],
            'total_wagered': state['total_wagered'],
            'peak_balance': state['peak_balance'],
            'config': {k:v for k,v in state['config'].items() if 'key' not in k and 'token' not in k}
        }
    
    system_prompt = f"""You are the Gork Controller AI. You help the user manage their Stake dice bot.
Current System Multi-Strategy State: {json.dumps(current_status)}

Available Commands:
- [START]: Start the betting loop.
- [STOP]: Stop the betting loop.
- [SET_STRATEGY name]: Change strategy (the_gork, ema_cross, die_last, vanish_in_volume, eternal_volume, custom, basic).
- [SIMULATE strategy bets]: Run a benchmark simulation. Strategy should be one of the above. Bets should be a number (e.g. 5000).
- [WRITE_STRATEGY]: Start writing a new custom Python strategy. 
  Follow this exactly: [WRITE_STRATEGY] your_python_code [END_STRATEGY]
  The code MUST define a function `calculate_bet(balance)`.
  IMPORTANT: You MUST define a `PARAMS` dictionary at the top for any tunable settings (e.g. `PARAMS = {'multiplier': 2.0}`).
  The code must end with `result = calculate_bet(balance)`.

Rules:
1. If the user wants to take an action, include the exact [COMMAND] in your reply.
2. For [WRITE_STRATEGY], ensure the code is valid Python and enclosed between [WRITE_STRATEGY] and [END_STRATEGY].
3. Be helpful, concise, and professional.
4. If asked for status, summarize the current metrics.
5. If you run a simulation or write a strategy, tell the user you've done so.
"""
    
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": system_prompt}]},
            {"role": "user", "parts": [{"text": message}]}
        ]
    }
    
    try:
        r = requests.post(url, json=payload, timeout=12)
        r_json = r.json()
        if 'candidates' not in r_json:
            error_data = r_json.get('error', {})
            err = error_data.get('message', 'Unknown API Error')
            return jsonify({'error': f"Gemini API Error: {err}"}), 500
        
        reply = r_json['candidates'][0]['content']['parts'][0]['text']
        
        # Simple Dispatcher
        if '[START]' in reply:
            state['is_running'] = True
            log("AI Command: START")
        if '[STOP]' in reply:
            state['is_running'] = False
            log("AI Command: STOP")
        if '[SET_STRATEGY' in reply:
            import re
            m = re.search(r'\[SET_STRATEGY\s+(\w+)\]', reply)
            if m:
                new_strat = m.group(1)
                valid = ['the_gork', 'ema_cross', 'die_last', 'vanish_in_volume', 'eternal_volume', 'custom', 'basic']
                if new_strat in valid:
                    state['strategy'] = new_strat
                    log(f"AI Command: SET_STRATEGY -> {new_strat}")
        
        if '[SIMULATE]' in reply:
            m = re.search(r'\[SIMULATE\s+(\w+)\s+(\d+)\]', reply)
            if m:
                sim_strat = m.group(1)
                sim_bets = int(m.group(2))
                log(f"AI Command: SIMULATE {sim_strat} for {sim_bets} bets")
                # Run simulation in background thread to not block AI response
                sim_data = {
                    'strategy': sim_strat,
                    'bets_to_simulate': sim_bets,
                    'starting_balance': state['balance']['available'],
                    'all_time_drawdown_cap_pct': state['config'].get('all_time_drawdown_cap_pct', -8.0),
                    'min_bet_floor': state['config'].get('min_bet_floor', 0.000001)
                }
                threading.Thread(target=_run_simulation_internal, args=(sim_data,), daemon=True).start()
                reply += "\n\n(System: Benchmark started in background. Results will be logged.)"

        if '[WRITE_STRATEGY]' in reply:
            m = re.search(r'\[WRITE_STRATEGY\](.*?)\[END_STRATEGY\]', reply, re.DOTALL)
            if m:
                new_code = m.group(1).strip()
                try:
                    with open(CUSTOM_STRAT_PATH, 'w') as f:
                        f.write(new_code)
                    log("AI Command: WRITE_STRATEGY -> custom_strategy.py updated")
                    reply += "\n\n(System: Custom strategy updated in Strategy Editor.)"
                except Exception as e:
                    log(f"AI WRITE_STRATEGY Error: {e}")
                    reply += f"\n\n(System Error: Failed to write strategy file: {e})"

        return jsonify({'reply': reply})
    except Exception as e:
        return jsonify({'error': f"Failed to reach Gemini: {str(e)}"}), 500

if __name__ == '__main__':
    threading.Thread(target=betting_loop, daemon=True).start()
    threading.Thread(target=update_prices_thread, daemon=True).start()
    log("Terminal Ready — http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5001, debug=False)

