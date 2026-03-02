import asyncio
import aiohttp
import random
import threading
import time
from flask import Flask, render_template, jsonify, request
from stake_api import Stake

app = Flask(__name__)

# --- GLOBAL STATE ---
state = {
    "api_key": "",
    "health": "DISCONNECTED",
    "bot": {
        "base_bet": 1.0,
        "win_chance": 99.0,
        "vault_floor": 1000.0,
        "target": 132500.0,
        "is_running": False
    },
    "wallet": {
        "active_coin": "usdt",
        "balances": {"usdt": 0.0, "btc": 0.0, "eth": 0.0},
        "bonuses": {"rakeback": 0.0, "reload": 0.0}
    },
    "stats": {
        "wagered": 0.0,
        "balance": 0.0
    },
    "logs": ["[SYSTEM] Engine Initialized."],
    "chart_data": [] # Stores history for the graph
}

def log_msg(msg):
    ts = time.strftime("%H:%M:%S")
    state["logs"].insert(0, f"[{ts}] {msg}")
    if len(state["logs"]) > 50: state["logs"].pop()

class GorkEngine:
    async def run(self):
        while True:
            if state["bot"]["is_running"] and state["health"] == "CONNECTED":
                # Simulated Betting Logic (Replace with actual Stake GraphQL)
                state["stats"]["wagered"] += state["bot"]["base_bet"]
                state["stats"]["balance"] += random.uniform(-0.1, 0.1) # Simulate minor variance
                log_msg(f"Bet placed: ${state['bot']['base_bet']} @ {state['bot']['win_chance']}%")
                
                # Record for chart
                state["chart_data"].append(state["stats"]["wagered"])
                if len(state["chart_data"]) > 20: state["chart_data"].pop(0)
                
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(2)

# --- ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/data', methods=['GET'])
def get_data():
    return jsonify(state)

@app.route('/api/health', methods=['POST'])
def update_health():
    key = request.json.get("api_key", "")
    state["api_key"] = key
    
    if len(key) > 10:
        try:
            stake = Stake(key)
            balances = stake.user_balances()
            
            if 'data' in balances and balances['data'] and 'user' in balances['data']:
                user_balances = balances['data']['user']['balances']
                parsed_balances = {}
                total_balance = 0.0
                
                for b in user_balances:
                    currency = b['available']['currency']
                    amount = float(b['available']['amount'])
                    parsed_balances[currency] = amount
                    # Assuming we just sum them naively or just take the main ones.
                    # We usually want to show the active coin. 
                    if currency == state["wallet"]["active_coin"]:
                        total_balance = amount
                
                state["health"] = "CONNECTED"
                # Keep whatever balances we already had, update with real ones
                state["wallet"]["balances"].update(parsed_balances)
                # Ensure the display balances uses the active coin logic or the sum
                state["stats"]["balance"] = total_balance
                log_msg("API Key accepted. Connected to node.")
            else:
                state["health"] = "DISCONNECTED"
                log_msg("ERROR: API returned invalid data (check key).")
        except Exception as e:
            state["health"] = "DISCONNECTED"
            log_msg(f"ERROR: Failed to connect to API ({str(e)}).")
    else:
        state["health"] = "DISCONNECTED"
        log_msg("ERROR: Invalid API Key format.")
        
    return jsonify({"health": state["health"]})

@app.route('/api/settings', methods=['POST'])
def save_settings():
    data = request.json
    state["bot"].update(data)
    log_msg("Bot settings updated & saved.")
    return jsonify({"success": True})

@app.route('/api/simulate', methods=['POST'])
def simulate_strategy():
    data = request.json
    bets = int(data.get("sim_bets", 1000))
    chance = float(data.get("sim_chance", 99.0))
    bet_size = float(data.get("sim_bet_size", 1.0))
    
    # Fast mathematical simulation
    wins = int(bets * (chance / 100))
    losses = bets - wins
    payout_multiplier = 99.0 / chance
    
    total_wagered = bets * bet_size
    profit = (wins * (bet_size * payout_multiplier)) - total_wagered
    
    log_msg(f"Simulation run: {bets} bets. Est PnL: ${profit:.2f}")
    return jsonify({"wagered": total_wagered, "profit": profit, "wins": wins, "losses": losses})

if __name__ == "__main__":
    threading.Thread(target=lambda: asyncio.run(GorkEngine().run()), daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
