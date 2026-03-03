# ========================================================
# THE GORK - Modular Edition (v2.0)
# Final Refactored Version
# ========================================================

import os
import json
import time
import threading
import logging
import random
import sqlite3
import hashlib
import hmac
import jwt
from functools import wraps
from datetime import datetime
from flask import Flask, jsonify, request, render_template, send_from_directory
from threading import Lock
import requests

# Modular Imports
from core.utils import stake_derive_roll, dragon_tower_derive_game, generate_new_seeds, calculate_ema
from core.engine import GorkEngine
from core.simulator import run_simulation_internal

DB_PATH = 'gork_data.db'
CUSTOM_STRAT_PATH = 'custom_strategy.py'

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS ai_history (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
        c.execute('CREATE TABLE IF NOT EXISTS chart_data (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, bet_number INTEGER, profit REAL, win_streak INTEGER, lose_streak INTEGER, balance REAL, ema5 REAL, ema20 REAL, roll_result REAL)')
        c.execute('''CREATE TABLE IF NOT EXISTS saved_strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            strategy TEXT NOT NULL,
            config TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        # Seed default custom strategy if missing
        res = c.execute("SELECT value FROM settings WHERE key='custom_strategy'").fetchone()
        if not res:
            default_template = """# Custom Python Strategy\ndef calculate_bet(balance):\n    # Return (amount, condition, target)\n    return balance * 0.001, "over", 50.50\nresult = calculate_bet(balance)"""
            c.execute("INSERT INTO settings (key, value) VALUES ('custom_strategy', ?)", (default_template,))

init_db()

# Stake API Wrapper (Mock or Real)
try:
    from stake_api.main import Stake
    api_available = True
except ImportError:
    api_available = False

API_TOKEN = os.getenv('STAKE_API_TOKEN', '')
if not API_TOKEN:
    with sqlite3.connect(DB_PATH) as conn:
        res = conn.execute("SELECT value FROM settings WHERE key='api_token'").fetchone()
        if res: API_TOKEN = res[0]

stake_client = None
if api_available and API_TOKEN and API_TOKEN != 'your_real_token_here':
    try: stake_client = Stake(API_TOKEN)
    except: pass

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
            return jsonify({'error': 'Token is missing or invalid.'}), 401
        try:
            token = token.split(' ')[1]
            jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
        except:
            return jsonify({'error': 'Token is invalid or expired.'}), 401
        return f(*args, **kwargs)
    return decorated

DEFAULT_CONFIG = {
    'base_bet_pct': 0.0012,
    'die_last_base_bet_pct': 0.005, 'die_last_tp_pct': 8.0, 'die_last_sl_pct': -3.5, 'die_last_daily_loss_cap_pct': -12.0,
    'vanish_base_bet_pct': 0.0015, 'vanish_tp_pct': 3.5, 'vanish_sl_pct': -2.0, 'vanish_daily_loss_cap_pct': -1.5,
    'eternal_base_bet_pct': 0.0012, 'eternal_tp_pct': 3.0, 'eternal_sl_pct': -1.4, 'eternal_daily_loss_cap_pct': -2.5,
    'session_tp_pct': 3.0, 'session_sl_pct': -1.4, 'daily_loss_cap_pct': -1.8,
    'weekly_loss_cap_pct': -4.0, 'all_time_drawdown_cap_pct': -8.0,
    'min_bet_floor': 0.000001, 'enable_seed_rotation': True, 'enable_daily_lock': True,
    'active_currency': 'btc', 'gemini_api_key': '',
    'basic_bet_amount': 0.000001, 'basic_on_win': 'reset', 'basic_win_mult': 1.0, 'basic_on_loss': 'multiply', 'basic_loss_mult': 2.0, 'basic_target': 50.50, 'basic_condition': 'over',
    'rm_base_bet_pct': 0.0012, 'rm_tp_pct': 3.0, 'rm_sl_pct': -8.0, 'rm_daily_loss_cap_pct': -10.0,
    'wg99_base_bet_pct': 0.05, 'wg99_tp_pct': 1.0, 'wg99_sl_pct': -15.0, 'wg99_daily_loss_cap_pct': -20.0,
    'fib_base_bet_pct': 0.001, 'fib_tp_pct': 2.0, 'fib_sl_pct': -5.0, 'fib_daily_loss_cap_pct': -10.0, 'fib_win_chance': 49.50,
    'par_base_bet_pct': 0.0005, 'par_tp_pct': 5.0, 'par_sl_pct': -3.0, 'par_daily_loss_cap_pct': -8.0, 'par_win_chance': 49.50, 'par_streak_target': 3,
    'osc_base_bet_pct': 0.001, 'osc_tp_pct': 2.5, 'osc_sl_pct': -4.0, 'osc_daily_loss_cap_pct': -8.0, 'osc_win_chance': 49.50,
    'dc_difficulty': 'easy', 'dc_target_col': 0
}

state = {
    'config': DEFAULT_CONFIG.copy(), 'strategy': 'the_gork', 'balance': {'available': 1000.0, 'currency': 'btc'},
    'is_running': False, 'current_bet': 0.000001, 'daily_start_balance': 1000.0, 'daily_start_time': time.time(),
    'peak_balance': 1000.0, 'recent_outcomes': [], 'current_win_streak': 0, 'current_lose_streak': 0,
    'total_bets': 0, 'total_wagered': 0.0, 'logs': [], 'roll_history': [],
    'prices': {'btc': 100000.0, 'ltc': 100.0, 'eth': 2500.0},
    'server_seed_hash': hashlib.sha256(os.urandom(32)).hexdigest(),
    'client_seed': f"gork-{random.randint(1000,9999)}", 'nonce': 0
}

# Engine Instance
engine = GorkEngine(state)

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    with state_lock:
        state['logs'].append(f"[{ts}] {msg}")
        state['logs'][:] = state['logs'][-300:]

def betting_loop():
    while True:
        if not state['is_running']:
            time.sleep(1); continue
        
        locked, reason = engine.is_locked()
        if locked:
            state['is_running'] = False
            log(f"LOCKED: {reason}"); continue
        
        with state_lock:
            cur_bal = state['balance']['available']
            bet, cond, tgt = engine.calculate_bet(state['strategy'], cur_bal)
            state['current_bet'] = bet

        # Real bet or mock
        roll = stake_derive_roll(state['server_seed_hash'], state['client_seed'], state['nonce'])
        won = roll > tgt if cond == "over" else roll < tgt
        
        with state_lock:
            state['nonce'] += 1
            state['total_bets'] += 1
            state['total_wagered'] += bet
            state['recent_outcomes'].append(won)
            state['roll_history'].append(roll)
            
            # Update streaks
            if won: 
                state['current_win_streak'] += 1; state['current_lose_streak'] = 0
                win_chance = (100.0 - tgt) if cond == 'over' else tgt
                mult = (100.0 / win_chance) * 0.99
                state['balance']['available'] += (bet * mult)
            else: 
                state['current_lose_streak'] += 1; state['current_win_streak'] = 0
                state['balance']['available'] -= bet

            if state['balance']['available'] > state['peak_balance']:
                state['peak_balance'] = state['balance']['available']

            # Calculate EMA for Chart
            with sqlite3.connect(DB_PATH) as conn:
                history = [r[0] for r in conn.execute("SELECT balance FROM chart_data ORDER BY id DESC LIMIT 50").fetchall()]
                history.reverse()
                history.append(state['balance']['available'])
                ema5 = calculate_ema(history, 5)
                ema20 = calculate_ema(history, 20)

                # DB Logging
                conn.execute("INSERT INTO chart_data (bet_number, profit, balance, roll_result, ema5, ema20) VALUES (?,?,?,?,?,?)", 
                            (state['total_bets'], state['balance']['available'] - state['daily_start_balance'], state['balance']['available'], roll, ema5, ema20))
            
        time.sleep(1.0)

# Start betting thread
threading.Thread(target=betting_loop, daemon=True).start()

# ROUTES
@app.route('/')
def dashboard():
    return render_template('dashboard.html', state=state)

@app.route('/login', methods=['POST'])
def login():
    pw = request.json.get('password')
    if pw == 'gork2026':
        token = jwt.encode({'user': 'admin', 'exp': time.time() + 86400}, app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'success': True, 'token': token})
    return jsonify({'success': False}), 401

@app.route('/status')
def get_status():
    return jsonify(state)

@app.route('/start', methods=['POST'])
@token_required
def start_bot():
    data = request.json
    with state_lock:
        state['config'].update(data)
        state['strategy'] = data.get('strategy', state['strategy'])
        state['is_running'] = True
    log(f"Bot STARTED: {state['strategy'].upper()}")
    return jsonify({'success': True})

@app.route('/stop', methods=['POST'])
@token_required
def stop_bot():
    with state_lock: state['is_running'] = False
    log("Bot STOPPED")
    return jsonify({'success': True})

@app.route('/simulate', methods=['POST'])
@token_required
def simulate():
    data = request.json
    results = run_simulation_internal(data, GorkEngine)
    return jsonify(results)

@app.route('/chart_data')
def get_chart_data():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT bet_number, profit, balance, roll_result, ema5, ema20 FROM chart_data ORDER BY id DESC LIMIT 1000").fetchall()
    return jsonify([{'bets':r[0], 'profit':r[1], 'balance':r[2], 'roll_result':r[3], 'ema5':r[4], 'ema20':r[5]} for r in reversed(rows)])

@app.route('/logs_data')
def get_logs_data():
    with state_lock:
        return jsonify({'logs': state['logs']})

@app.route('/prices_data')
def get_prices_data():
    with state_lock:
        return jsonify(state['prices'])

# Settings & Wallet Routes
@app.route('/settings/vip')
def get_vip():
    return jsonify({'success': True, 'vipLevel': 'Platinum III', 'wagerProgress': 74.2})

@app.route('/settings/wallets')
def get_wallets():
    return jsonify({
        'success': True,
        'wallets': [
            {'currency': 'btc', 'amount': state['balance']['available'], 'usd': state['balance']['available'] * state['prices']['btc']},
            {'currency': 'ltc', 'amount': 15.5, 'usd': 15.5 * state['prices']['ltc']},
            {'currency': 'eth', 'amount': 0.42, 'usd': 0.42 * state['prices']['eth']}
        ]
    })

@app.route('/settings/set_wallet', methods=['POST'])
@token_required
def set_wallet():
    curr = request.json.get('currency', 'btc')
    with state_lock:
        state['balance']['currency'] = curr
    return jsonify({'success': True})

@app.route('/settings/gemini_key', methods=['POST'])
@token_required
def set_gemini_key():
    key = request.json.get('key')
    with state_lock:
        state['config']['gemini_api_key'] = key
    return jsonify({'success': True, 'message': 'API Key updated locally.'})

# Strategy Management
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
    name, strat, cfg = d.get('name','').strip(), d.get('strategy',''), d.get('config', {})
    if not name or not strat: return jsonify({'error':'Missing name or strategy'}), 400
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO saved_strategies (name, strategy, config) VALUES (?,?,?)", (name, strat, json.dumps(cfg)))
    return jsonify({'success': True})

@app.route('/strategies/<int:id>', methods=['GET'])
@token_required
def get_strategy(id):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT name, strategy, config FROM saved_strategies WHERE id=?", (id,)).fetchone()
    if row: return jsonify({'name':row[0], 'strategy':row[1], 'config':json.loads(row[2])})
    return jsonify({'error':'Not found'}), 404

@app.route('/custom_strategy', methods=['GET', 'POST'])
@token_required
def route_custom_strategy():
    if request.method == 'POST':
        code = request.json.get('code')
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('custom_strategy', ?)", (code,))
        return jsonify({'success': True})
    else:
        with sqlite3.connect(DB_PATH) as conn:
            res = conn.execute("SELECT value FROM settings WHERE key='custom_strategy'").fetchone()
        return jsonify({'code': res[0] if res else ""})

@app.route('/strategy_templates')
def list_templates():
    return jsonify(['flat_bet', 'martingale', 'streak_hunter', 'ema_trend'])

@app.route('/strategy_templates/<name>')
def get_template(name):
    templates = {
        'flat_bet': "def calculate_bet(balance):\n    return 0.000001, 'over', 50.50",
        'martingale': "def calculate_bet(balance):\n    if state.get('last_won', True):\n        state['current_bet'] = 0.000001\n    else:\n        state['current_bet'] *= 2\n    return state['current_bet'], 'over', 50.50",
    }
    return jsonify({'code': templates.get(name, "# Template not found")})

@app.route('/strategy/params')
def get_params():
    # In a real app, parse the custom_strategy for a PARAMS dict
    return jsonify({'base_mult': 1.0, 'risk_factor': 0.5})

# Prediction Routes
@app.route('/api/dice/predict', methods=['POST'])
@token_required
def dice_predict():
    data = request.json
    roll = stake_derive_roll(data['server_seed_hash'], data['client_seed'], data['nonce'])
    return jsonify({'roll': roll})

@app.route('/api/dragon_tower/predict', methods=['POST'])
def dragon_predict():
    data = request.json
    tower = dragon_tower_derive_game(data.get('server_seed', state['server_seed_hash']), 
                                    data.get('client_seed', state['client_seed']), 
                                    int(data.get('nonce', state['nonce'])), 
                                    data.get('difficulty', 'easy'))
    return jsonify({'success': True, 'tower': tower})

@app.route('/api/manual_bet', methods=['POST'])
def manual_bet():
    data = request.json
    # Simulate a manual bet
    amount = data.get('amount', 0.000001)
    game = data.get('game', 'dice')
    with state_lock:
        state['balance']['available'] -= amount
        # Simplified win logic
        won = random.random() > 0.5
        payout = amount * 1.98 if won else 0
        state['balance']['available'] += payout
    return jsonify({'success': True, 'amount': amount, 'payout': payout, 'multiplier': 1.98 if won else 0})

@app.route('/ai/chat', methods=['POST'])
@token_required
def ai_chat():
    data = request.json
    msg = data.get('message', '').lower()
    reply = "AI Controller online. Systems functional."
    if "status" in msg: reply = f"Current balance: {state['balance']['available']:.8f} {state['balance']['currency'].upper()}. Bot is {'RUNNING' if state['is_running'] else 'PAUSED'}."
    return jsonify({'reply': reply})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
