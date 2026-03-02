import requests

pw = "admin"
token = requests.post("http://127.0.0.1:5001/login", json={"password":pw}).json().get("token")

payload_wg = {
    "strategy": "wager_grind_99",
    "starting_balance": 1000.0,
    "bets_to_simulate": 100,
    "all_time_drawdown_cap_pct": -90.0,
    "min_bet_floor": 0.000001,
    "wg99_base_bet_pct": 0.05,
    "wg99_tp_pct": 100.0,
    "wg99_sl_pct": -90.0,
    "wg99_daily_loss_cap_pct": -90.0
}

res = requests.post("http://127.0.0.1:5001/simulate", json=payload_wg, headers={"Authorization": f"Bearer {token}"})
try:
    data = res.json()
    print(data)
except Exception as e:
    print("Error parsing json:", res.text)
