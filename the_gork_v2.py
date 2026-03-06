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
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from functools import wraps
from datetime import datetime
from flask import Flask, jsonify, request, render_template, send_from_directory
from threading import Lock
import requests

# Modular Imports
from core.utils import stake_derive_roll, dragon_tower_derive_game, generate_new_seeds, calculate_ema
from core.engine import GorkEngine
from core.simulator import run_simulation_internal
from core.schemas import (GorkConfig, StartBotRequest, LoginRequest, SaveStrategyRequest, 
                          DicePredictRequest, DragonPredictRequest, ManualBetRequest,
                          SetWalletRequest, SetGeminiKeyRequest)
from pydantic import ValidationError
from flask_socketio import SocketIO, emit
import time as _time
from collections import defaultdict, deque
from threading import Lock as _ThreadLock

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
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')

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
# Load secret key from environment variable for security
app.config['SECRET_KEY'] = os.getenv('GORK_JWT_SECRET', 'change_this_to_a_secure_random_string_in_production')
from gork_state import state, DEFAULT_CONFIG, state_lock

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

# State is now maintained in gork_state.py

# Engine Instance
engine = GorkEngine(state)
from strategy_terminal import StrategyTerminal
# Terminal instance wired to live state
terminal = StrategyTerminal(CUSTOM_STRAT_PATH, state=state)

# SocketIO for live terminal
socketio = SocketIO(app, cors_allowed_origins="*")

# Simple rate limiter per-socket id (requests per window)
RATE_LIMIT_COUNT = 8
RATE_LIMIT_WINDOW = 10.0  # seconds
_rate_map = defaultdict(lambda: deque())
_rate_lock = _ThreadLock()

def check_rate_limit(key):
    now = _time.time()
    with _rate_lock:
        dq = _rate_map[key]
        # drop old
        while dq and dq[0] < now - RATE_LIMIT_WINDOW:
            dq.popleft()
        if len(dq) >= RATE_LIMIT_COUNT:
            return False
        dq.append(now)
        return True

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
            
            # Deduct bet BEFORE the roll to match reality
            bet = min(bet, cur_bal)
            state['current_bet'] = bet
            state['balance']['available'] -= bet

        # Real bet or mock
        roll = stake_derive_roll(state['server_seed_hash'], state['client_seed'], state['nonce'])
        won = roll > tgt if cond == "over" else roll < tgt
        win_chance = (100.0 - tgt) if cond == 'over' else tgt
        multiplier = (100.0 / win_chance) * 0.99
        
        with state_lock:
            state['nonce'] += 1
            state['total_bets'] += 1
            state['total_wagered'] += bet
            state['recent_outcomes'].append(won)
            state['roll_history'].append(roll)
            
            # Engine handles streaks, balance additions, and strategy memory
            engine.update_state(won, roll, bet, multiplier)

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

def update_prices_thread():
    while True:
        try:
            # Mock or real price fetch
            symbols = ["BTCUSDT", "LTCUSDT", "ETHUSDT"]
            price_map = {}
            for s in symbols:
                r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={s}", timeout=5)
                if r.status_code == 200:
                    price_map[s.replace("USDT", "").lower()] = float(r.json()['price'])
            
            with state_lock:
                for coin, price in price_map.items():
                    state['prices'][coin] = price
        except Exception as e:
            logger.error(f"Price update failed: {e}")
        time.sleep(60)

# Start threads
threading.Thread(target=betting_loop, daemon=True).start()
threading.Thread(target=update_prices_thread, daemon=True).start()

# ROUTES
@app.route('/')
def dashboard():
    return render_template('dashboard.html', state=state)

@app.route('/login', methods=['POST'])
def login():
    data = request.json or {}
    # Support three login methods:
    # 1) local username/password (default)
    # 2) api_token: provide Stake API token to use for API calls
    # 3) stake_username + stake_password: attempt to authenticate via stake_api wrapper (if available)
    try:
        # API token flow
        if data.get('api_token'):
            token_val = data.get('api_token')
            global API_TOKEN, stake_client
            API_TOKEN = token_val
            # persist token
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('api_token', ?)", (API_TOKEN,))
            except Exception:
                logger.exception('Failed to persist api_token')

            if api_available:
                try:
                    stake_client = Stake(API_TOKEN)
                except Exception:
                    stake_client = None
            token = jwt.encode({'user': 'api_user', 'exp': time.time() + 86400}, app.config['SECRET_KEY'], algorithm="HS256")
            return jsonify({'success': True, 'token': token, 'note': 'API token registered locally.'})

        # Stake username/password flow (if the Stake wrapper supports a login method)
        if data.get('stake_username') and data.get('stake_password'):
            if not api_available:
                return jsonify({'success': False, 'error': 'Stake API wrapper not available on server.'}), 400
            try:
                # Try to use a login method if provided by the wrapper
                if hasattr(Stake, 'login'):
                    sc = Stake()
                    res = sc.login(data.get('stake_username'), data.get('stake_password'))
                    # if login returns a token, store it
                    if isinstance(res, str):
                        API_TOKEN = res
                        stake_client = Stake(API_TOKEN)
                        with sqlite3.connect(DB_PATH) as conn:
                            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('api_token', ?)", (API_TOKEN,))
                    token = jwt.encode({'user': data.get('stake_username'), 'exp': time.time() + 86400}, app.config['SECRET_KEY'], algorithm="HS256")
                    return jsonify({'success': True, 'token': token})
                else:
                    return jsonify({'success': False, 'error': 'Stake wrapper does not support username/password login.'}), 400
            except Exception as e:
                logger.exception('Stake login failed')
                return jsonify({'success': False, 'error': f'Stake login failed: {e}'}), 500

        # Fallback: local username/password
        req = LoginRequest(**data)
        valid_user = os.getenv('GORK_USERNAME', 'admin')
        valid_pass = os.getenv('GORK_PASSWORD', 'admin123')
        if req.username == valid_user and req.password == valid_pass:
            token = jwt.encode({'user': valid_user, 'exp': time.time() + 86400}, app.config['SECRET_KEY'], algorithm="HS256")
            return jsonify({'success': True, 'token': token})
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
    except ValidationError as e:
        return jsonify({'success': False, 'error': e.errors()}), 400

@app.route('/status')
def get_status():
    return jsonify(state)

@app.route('/start', methods=['POST'])
@token_required
def start_bot():
    try:
        req = StartBotRequest(**request.json)
        with state_lock:
            if req.config:
                # Update existing config with new values, validating the result
                current_cfg = state['config'].copy()
                current_cfg.update(req.config)
                validated_cfg = GorkConfig(**current_cfg)
                state['config'] = validated_cfg.dict()
            
            state['strategy'] = req.strategy
            state['is_running'] = True
        log(f"Bot STARTED: {state['strategy'].upper()}")
        return jsonify({'success': True})
    except ValidationError as e:
        return jsonify({'success': False, 'error': e.errors()}), 400

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

@app.route('/api/convert', methods=['GET'])
def convert_usd():
    usd = request.args.get('usd', 1.0, type=float)
    currency = state['balance']['currency'].lower()
    price = state['prices'].get(currency, 1.0)
    coin_amount = usd / price if price > 0 else 0
    return jsonify({'usd': usd, 'currency': currency, 'amount': coin_amount})

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
    try:
        req = SetWalletRequest(**request.json)
        with state_lock:
            state['balance']['currency'] = req.currency
        return jsonify({'success': True})
    except ValidationError as e:
        return jsonify({'success': False, 'error': e.errors()}), 400

@app.route('/settings/gemini_key', methods=['POST'])
@token_required
def set_gemini_key():
    try:
        req = SetGeminiKeyRequest(**request.json)
        with state_lock:
            state['config']['gemini_api_key'] = req.key
        return jsonify({'success': True, 'message': 'API Key updated locally.'})
    except ValidationError as e:
        return jsonify({'success': False, 'error': e.errors()}), 400

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
    try:
        req = SaveStrategyRequest(**(request.json or {}))
        # Validate the config inside the strategy as well
        if req.config:
            GorkConfig(**req.config)
            
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO saved_strategies (name, strategy, config) VALUES (?,?,?)", 
                         (req.name, req.strategy, json.dumps(req.config)))
        return jsonify({'success': True})
    except ValidationError as e:
        return jsonify({'success': False, 'error': e.errors()}), 400

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


# Web Terminal Endpoints
@app.route('/terminal')
@token_required
def terminal_page():
    return render_template('terminal.html')


@app.route('/terminal/exec', methods=['POST'])
@token_required
def terminal_exec():
    data = request.json or {}
    cmd = data.get('cmd')
    if cmd == 'show':
        with state_lock:
            return jsonify(state)
    if cmd == 'run':
        res = terminal.run_strategy()
        return jsonify({'result': res})
    if cmd == 'load':
        path = data.get('path')
        code = data.get('code')
        if code:
            with open(CUSTOM_STRAT_PATH, 'w', encoding='utf-8') as f:
                f.write(code)
            terminal.load_strategy(CUSTOM_STRAT_PATH)
            return jsonify({'success': True, 'msg': 'Saved and loaded custom strategy.'})
        if path:
            terminal.load_strategy(path)
            return jsonify({'success': True, 'msg': f'Loaded {path}'})
        return jsonify({'error': 'No path or code provided'}), 400
    if cmd == 'get_code':
        try:
            with open(CUSTOM_STRAT_PATH, 'r', encoding='utf-8') as f:
                return jsonify({'code': f.read()})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'Unknown command'}), 400


# SocketIO handlers for interactive terminal
@socketio.on('connect')
def _socket_connect(auth):
    token = None
    if isinstance(auth, dict):
        token = auth.get('token') or auth.get('auth')
    if not token:
        return False
    try:
        jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
    except Exception:
        return False
    socketio.emit('output', {'msg': 'Terminal socket connected'})


@socketio.on('cmd')
def _socket_cmd(data):
    sid = request.sid
    if not check_rate_limit(sid):
        socketio.emit('output', {'msg': 'Rate limit exceeded, slow down.'}, to=sid)
        return
    try:
        cmd = (data or {}).get('cmd')
        if cmd == 'show':
            with state_lock:
                socketio.emit('output', {'msg': json.dumps(state, default=str)}, to=sid)
            return
        if cmd == 'run':
            res = terminal.run_strategy()
            socketio.emit('output', {'msg': f'Run result: {res}'}, to=sid)
            return
        if cmd == 'get_code':
            code = terminal.get_code()
            socketio.emit('output', {'msg': code or 'No code available'}, to=sid)
            return
        if cmd == 'load':
            code = data.get('code')
            path = data.get('path')
            if code:
                ok = terminal.save_code(code)
                socketio.emit('output', {'msg': 'Saved code' if ok else 'Save failed'}, to=sid)
                return
            if path:
                terminal.load_strategy(path)
                socketio.emit('output', {'msg': f'Loaded {path}'}, to=sid)
                return
            socketio.emit('output', {'msg': 'No path or code provided'}, to=sid)
            return
        if cmd == 'history':
            socketio.emit('output', {'msg': json.dumps(terminal.get_history(), default=str)}, to=sid)
            return
        if cmd == 'exec':
            code = data.get('code', '')

            def stream_cb(tag, text):
                socketio.emit('output', {'msg': f'[{tag}] {text}'}, to=sid)

            # run in background and stream
            socketio.start_background_task(lambda: _run_and_stream(code, stream_cb))
            return
        socketio.emit('output', {'msg': f'Unknown cmd: {cmd}'}, to=sid)
    except Exception as e:
        socketio.emit('output', {'msg': f'Error: {e}'}, to=sid)


def _run_and_stream(code, stream_cb):
    # use subprocess runner and forward outputs via stream_cb
    try:
        rc, summary = terminal.exec_code_subprocess(code, timeout=8.0, stream_callback=stream_cb)
        stream_cb('sys', summary + f' (rc={rc})')
    except Exception as e:
        stream_cb('err', str(e))

# Prediction Routes
@app.route('/api/dice/predict', methods=['POST'])
@token_required
def dice_predict():
    try:
        req = DicePredictRequest(**request.json)
        roll = stake_derive_roll(req.server_seed_hash, req.client_seed, req.nonce)
        return jsonify({'roll': roll})
    except ValidationError as e:
        return jsonify({'success': False, 'error': e.errors()}), 400

@app.route('/api/dragon_tower/predict', methods=['POST'])
def dragon_predict():
    try:
        req = DragonPredictRequest(**request.json)
        tower = dragon_tower_derive_game(
            req.server_seed or state['server_seed_hash'], 
            req.client_seed or state['client_seed'], 
            req.nonce if req.nonce is not None else int(state['nonce']), 
            req.difficulty
        )
        return jsonify({'success': True, 'tower': tower})
    except ValidationError as e:
        return jsonify({'success': False, 'error': e.errors()}), 400

@app.route('/api/manual_bet', methods=['POST'])
@token_required
def manual_bet():
    try:
        req = ManualBetRequest(**request.json)
        amount = req.amount
        game = req.game
        
        # Real Stake API logic if available, else mock
        if stake_client:
            try:
                active_curr = state['config'].get('active_currency', 'btc').lower()
                if game == 'dice':
                    res = stake_client.dice_roll(amount, "above", 50.50, active_curr)
                elif game == 'limbo':
                    res = stake_client.limbo_roll(amount, 2.0, active_curr)
                elif game == 'plinko':
                    res = stake_client.plinko_roll(amount, "medium", 12, active_curr)
                elif game == 'keno':
                    # Mock numbers for keno manual bet if not provided
                    nums = request.json.get('numbers', [1, 2, 3, 4, 5])
                    res = stake_client.keno_roll(amount, nums, active_curr)
                else:
                    return jsonify({'error': f'Game {game} not supported for API betting.'}), 400

                if 'errors' in res:
                    return jsonify({'error': res['errors'][0]['message']}), 400
                
                # Standardize response (simplified)
                bet_data = res.get('data', {}).get(f"{game}Bet", {})
                return jsonify({
                    'success': True,
                    'amount': amount,
                    'payout': bet_data.get('payout', 0),
                    'multiplier': bet_data.get('payoutMultiplier', 0)
                })
            except Exception as e:
                log(f"API Bet Error: {e}")
                # Fallback to mock on API fail? No, better to report error.
                return jsonify({'error': str(e)}), 500

        # Mock Logic
        with state_lock:
            state['balance']['available'] -= amount
            won = random.random() > 0.52 # House edge
            payout = amount * 1.98 if won else 0
            state['balance']['available'] += payout
        return jsonify({'success': True, 'amount': amount, 'payout': payout, 'multiplier': 1.98 if won else 0})
    except ValidationError as e:
        return jsonify({'success': False, 'error': e.errors()}), 400

@app.route('/ai/chat', methods=['POST'])
@token_required
def ai_chat():
    data = request.json
    message = data.get('message', '')
    if not message:
        return jsonify({'error': 'No message provided'}), 400
    
    api_key = state['config'].get('gemini_api_key') or GEMINI_API_KEY
    if not api_key:
        return jsonify({'error': 'Gemini API Key not configured.'}), 400

    # Build Context
    with state_lock:
        current_status = {
            'is_running': state['is_running'],
            'strategy': state['strategy'],
            'balance': state['balance'],
            'total_bets': state['total_bets'],
            'total_wagered': state['total_wagered'],
            'peak_balance': state['peak_balance']
        }
    
    system_prompt = f"""You are Gork Controller AI. Help manage the Stake bot.
Current State: {json.dumps(current_status)}

Available Commands:
- [START]: Start the betting loop.
- [STOP]: Stop the betting loop.
- [SET_STRATEGY name]: Change strategy (the_gork, ema_cross, die_last, vanish_in_volume, eternal_volume, custom, basic).
- [SIMULATE strategy bets]: Run a benchmark simulation. Strategy should be one of the above. Bets should be a number (e.g. 5000).
- [WRITE_STRATEGY]: Start writing a new custom Python strategy. 
  Follow this exactly: [WRITE_STRATEGY] your_python_code [END_STRATEGY]
  The code MUST define a function `calculate_bet(balance)`.
  The code must end with `result = calculate_bet(balance)`.

Rules:
1. If the user wants to take an action, include the exact [COMMAND] in your reply.
2. Be helpful, concise, and professional.
3. If asked for status, summarize the current metrics.
"""
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
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
            error_msg = r_json.get('error', {}).get('message', 'Unknown Gemini Error')
            if 'SAFETY' in str(r_json): error_msg = "Content filtered by Safety Settings."
            return jsonify({'error': f"Gemini API Error: {error_msg}"}), 500
            
        reply = r_json['candidates'][0]['content']['parts'][0]['text']
        
        # Dispatcher logic
        if '[START]' in reply:
            with state_lock: state['is_running'] = True
            log("AI Command: START")
        if '[STOP]' in reply:
            with state_lock: state['is_running'] = False
            log("AI Command: STOP")
        if '[SET_STRATEGY' in reply:
            import re
            m = re.search(r'\[SET_STRATEGY\s+(\w+)\]', reply)
            if m:
                new_strat = m.group(1)
                valid = ['the_gork', 'ema_cross', 'die_last', 'vanish_in_volume', 'eternal_volume', 'custom', 'basic']
                if new_strat in valid:
                    with state_lock: state['strategy'] = new_strat
                    log(f"AI: SET_STRATEGY -> {new_strat}")

        if '[SIMULATE]' in reply:
            import re
            m = re.search(r'\[SIMULATE\s+(\w+)\s+(\d+)\]', reply)
            if m:
                sim_strat = m.group(1)
                sim_bets = int(m.group(2))
                log(f"AI Command: SIMULATE {sim_strat} for {sim_bets} bets")
                sim_data = {
                    'strategy': sim_strat,
                    'bets_to_simulate': sim_bets,
                    'starting_balance': state['balance']['available'],
                    'all_time_drawdown_cap_usd': state['config'].get('all_time_drawdown_cap_usd', -100.0),
                    'min_bet_floor': state['config'].get('min_bet_floor', 0.000001)
                }
                threading.Thread(target=run_simulation_internal, args=(sim_data, GorkEngine), daemon=True).start()
                reply += "\n\n(System: Benchmark started in background.)"

        if '[WRITE_STRATEGY]' in reply:
            import re
            m = re.search(r'\[WRITE_STRATEGY\](.*?)\[END_STRATEGY\]', reply, re.DOTALL)
            if m:
                new_code = m.group(1).strip()
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('custom_strategy', ?)", (new_code,))
                log("AI Command: WRITE_STRATEGY -> Database updated")
                reply += "\n\n(System: Custom strategy updated in Strategy Editor.)"

        return jsonify({'reply': reply})
    except Exception as e:
        return jsonify({'error': f"Failed to reach Gemini: {str(e)}"}), 500


if __name__ == '__main__':
    # Use SocketIO runner when available so websockets work
    try:
        socketio.run(app, host='0.0.0.0', port=5001, debug=False)
    except Exception:
        app.run(host='0.0.0.0', port=5001, debug=False)
