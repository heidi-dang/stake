# ✧ THE GORK v2.0 — Stake.com Autobetting Bot

**The Gork** is a premium, dashboard-controlled autobetting bot for Stake.com. It features multiple advanced betting strategies, a real-time charting system, and a strategy editor.

> [!WARNING]
> **Financial Risk**: Automated betting involves high risk. Never bet more than you can afford to lose.
> **Terms of Service**: Use of this tool violates Stake.com's Terms of Service. Use at your own risk.

---

## 🚀 Getting Started

### 1. Prerequisites
- **Python 3.8+**
- A valid **Stake.com API Token** (found in your Stake account settings under API).

### 2. Installation
Clone this repository and install the required dependencies:
```bash
pip install flask requests pyjwt pandas
```

### 3. Configuration
You can set your Stake API token in two ways:
1. **Environment Variable**: 
   ```bash
   export STAKE_API_TOKEN="your_real_token_here"
   ```
2. **Dashboard**: Enter your token directly into the "Settings" tab once the bot is running.

---

## 🛠 Usage

### Launching the Bot
Run the main script to start the Flask web dashboard:
```bash
python the_gork_v2.py
```
By default, the dashboard will be available at: `http://localhost:5000`

### Dashboard Overview
- **Terminal**: Monitor real-time status, balance, and logs.
- **Charting**: Visualize your profit, win streaks, and EMA crossovers in real-time.
- **Simulator**: Test strategies with simulated funds before going live.
- **Strategy Editor**: Write or modify custom Python betting strategies.
- **Settings**: Configure API tokens, active currencies, and global loss caps.

### Real API Betting
To enable real betting:
1. Ensure `STAKE_API_TOKEN` is set.
2. Select your **Active Currency** in the Settings tab.
3. Click **Initialize** on the Terminal tab. 
4. The bot will automatically detect your balance and start placing bets based on your selected strategy.

---

## 🧠 Strategies

- **THE GORK (Flagship)**: Mirror balance recovery strategy.
- **DIE LAST**: High-risk, high-reward streak-based strategy.
- **VANISH IN VOLUME**: Designed to generate high wager volume with low drawdown.
- **EMA CROSS**: Technical analysis strategy using Exponential Moving Averages.
- **CUSTOM**: Write your own logic in the Strategy Editor using Python.

---

## 🛡 Security & Safety
- **No Data Exfiltration**: This bot only communicates with `stake.com` and `binance.com` (for price updates).
- **Local Storage**: All session tokens and history are stored locally in `gork_data.db` and `.pkl` files.
- **Loss Caps**: Set daily, weekly, and all-time loss caps in the Settings tab to automatically halt the bot.

---

## ⚖ License
This project is for educational and personal use only. The author is not responsible for any financial losses incurred.
