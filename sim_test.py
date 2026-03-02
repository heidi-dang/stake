import requests

pw = "admin"
try:
    token = requests.post("http://127.0.0.1:5001/login", json={"password":pw}).json().get("token")
except Exception as e:
    print("Failed to login:", e)
    exit(1)

payload_wg = {
    "strategy": "wager_grind_99",
    "starting_balance": 1000.0,
    "bets_to_simulate": 100000,
    "all_time_drawdown_cap_pct": -90.0,
    "min_bet_floor": 0.000001,
    "wg99_base_bet_pct": 0.05,
    "wg99_tp_pct": 100.0,
    "wg99_sl_pct": -90.0,
    "wg99_daily_loss_cap_pct": -90.0
}

payload_rm = {
    "strategy": "reverted_martingale",
    "starting_balance": 1000.0,
    "bets_to_simulate": 100000,
    "all_time_drawdown_cap_pct": -90.0,
    "min_bet_floor": 0.000001,
    "rm_base_bet_pct": 0.0012,
    "rm_tp_pct": 100.0,
    "rm_sl_pct": -90.0,
    "rm_daily_loss_cap_pct": -90.0,
    "rm_mult_on_loss": 0.5,
    "rm_mult_on_win": 1.0
}

def run_test(name, payload):
    res = requests.post("http://127.0.0.1:5001/simulate", json=payload, headers={"Authorization": f"Bearer {token}"})
    data = res.json()
    print(f"--- {name} [100,000 Bets] ---")
    print("Status:", data.get('status', 'OK'))
    print("Final Bal: $", data.get('final_balance'))
    print("Wagered:   $", data.get('total_wagered'))
    print("Peak:      $", data.get('peak_balance'))
    print("Total Bets:", data.get('sim_bets'))
    print("Win Rate:  ", round((data.get('wins', 0) / max(1, data.get('sim_bets', 1))) * 100, 2), "%")
    print("---------------------------------")

run_test("WAGER GRIND 99", payload_wg)
run_test("REVERTED MARTINGALE", payload_rm)
