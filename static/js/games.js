// Predictors and Games logic
async function predictDice() {
    const server = document.getElementById('dice_pred_server').value;
    const client = document.getElementById('dice_pred_client').value;
    const nonce = document.getElementById('dice_pred_nonce').value;
    const targetEl = document.getElementById('dice_pred_target');
    const condEl = document.getElementById('dice_pred_condition');
    if (!targetEl || !condEl) return;

    const target = parseFloat(targetEl.value);
    const cond = condEl.value;

    if (!server || !client) { alert("Please provide seeds."); return; }

    const r = await fetch('/api/dice/predict', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + localStorage.getItem('gork_jwt') },
        body: JSON.stringify({ server_seed: server, client_seed: client, nonce: parseInt(nonce) })
    });
    const data = await r.json();
    if (data.roll !== undefined) {
        const roll = data.roll;
        const visual = document.getElementById('dice_pred_visual');
        const status = document.getElementById('dice_pred_status');
        if (!visual || !status) return;

        visual.innerText = roll.toFixed(2);
        let win = false;
        if (cond === 'over' && roll > target) win = true;
        if (cond === 'under' && roll < target) win = true;

        if (win) {
            visual.style.color = 'var(--success)';
            status.innerText = "WINNER";
            status.style.color = 'var(--success)';
        } else {
            visual.style.color = 'var(--danger)';
            status.innerText = "LOSER";
            status.style.color = 'var(--danger)';
        }
    }
}

async function predictDragon() {
    const s_seed = document.getElementById('dt_server_seed').value;
    const c_seed = document.getElementById('dt_client_seed').value;
    const nonce = document.getElementById('dt_nonce').value;
    const diffEl = document.getElementById('dt_difficulty');
    if (!diffEl) return;
    const difficulty = diffEl.value;

    if (!s_seed || !c_seed) return; // Silent return if seeds missing during auto-update

    const res = await fetch('/api/dragon_tower/predict', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_seed: s_seed, client_seed: c_seed, nonce: nonce, difficulty: difficulty })
    });
    const data = await res.json();

    if (data.success) {
        const viz = document.getElementById('tower-viz');
        if (viz) {
            const cols = (difficulty === 'medium' || difficulty === 'expert') ? 3 : (difficulty === 'hard' ? 2 : 4);
            viz.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
        }
        renderTower(data.tower);
    }
}

function renderTower(tower) {
    const viz = document.getElementById('tower-viz');
    if (!viz) return;
    viz.innerHTML = '';
    // Reverse the tower so row 9 is at top
    const displayTower = [...tower].reverse();
    displayTower.forEach((row, rowIdx) => {
        row.forEach((tile, colIdx) => {
            const tileDiv = document.createElement('div');
            tileDiv.className = 'tower-tile ' + (tile.is_egg ? 'egg' : 'safe');
            tileDiv.innerHTML = `<span>${tile.is_egg ? '💣' : '💎'}</span>`;
            viz.appendChild(tileDiv);
        });
    });
}

function playManualGame(game) {
    let amount, payload = { game: game };
    let resDiv = document.getElementById(game + '_manual_result');
    if (!resDiv) return;

    if (game === 'dice') {
        amount = parseFloat(document.getElementById('dice_manual_bet').value);
        payload.amount = amount;
        payload.target = parseFloat(document.getElementById('dice_manual_target').value);
        payload.condition = document.getElementById('dice_manual_condition').value;
    } else if (game === 'limbo') {
        amount = parseFloat(document.getElementById('limbo_manual_bet').value);
        payload.amount = amount;
        payload.multiplier = parseFloat(document.getElementById('limbo_manual_mult').value);
    } else if (game === 'plinko') {
        amount = parseFloat(document.getElementById('plinko_manual_bet').value);
        payload.amount = amount;
        payload.risk = document.getElementById('plinko_manual_risk').value;
        payload.rows = parseInt(document.getElementById('plinko_manual_rows').value);
    } else if (game === 'keno') {
        amount = parseFloat(document.getElementById('keno_manual_bet').value);
        payload.amount = amount;
        let selectedNums = [];
        document.querySelectorAll('#keno-grid .keno-num.selected').forEach(el => {
            selectedNums.push(parseInt(el.dataset.num));
        });
        payload.numbers = selectedNums;
        if (payload.numbers.length === 0) {
            resDiv.style.color = 'var(--danger)';
            resDiv.innerHTML = 'Select at least one number.';
            return;
        }
    } else { return; }

    resDiv.style.color = 'var(--secondary)';
    resDiv.innerHTML = 'Processing...';

    fetch('/api/manual_bet', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).then(r => r.json()).then(data => {
        if (data.error) {
            resDiv.style.color = 'var(--danger)';
            resDiv.innerHTML = 'Error: ' + data.error;
            return;
        }
        const isWin = data.payout > 0;
        resDiv.style.color = isWin ? 'var(--primary)' : 'var(--danger)';
        resDiv.innerHTML = isWin ? `WON +${(data.payout - data.amount).toFixed(8)}! (x${data.multiplier})` : `LOST -${data.amount.toFixed(8)}`;

        resDiv.style.animation = 'none';
        resDiv.offsetHeight;
        resDiv.style.animation = 'fadeIn 0.5s';

        if (typeof updateDashboardData === 'function') updateDashboardData();
    }).catch(e => {
        resDiv.style.color = 'var(--danger)';
        resDiv.innerHTML = 'Error communicating with server.';
    });
}

function saveGeminiKey() {
    const key = document.getElementById('gemini_key').value;
    fetch('/settings/gemini_key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key })
    }).then(r => r.json()).then(d => {
        alert(d.message || d.error);
    });
}

function sendAiCommand() {
    const input = document.getElementById('ai-input');
    if (!input) return;
    const msg = input.value.trim();
    if (!msg) return;

    const history = document.getElementById('ai-chat-history');
    if (history) {
        history.innerHTML += `<div class="ai-msg ai-msg-user"><b>You:</b> ${msg}</div>`;
        history.scrollTop = history.scrollHeight;
    }
    input.value = '';

    fetch('/ai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg })
    }).then(r => r.json()).then(d => {
        if (history) {
            if (d.error) {
                history.innerHTML += `<div class="ai-msg ai-msg-bot" style="color:var(--danger);"><b>Error:</b> ${d.error}</div>`;
            } else {
                history.innerHTML += `<div class="ai-msg ai-msg-bot"><b>AI:</b> ${d.reply}</div>`;
            }
            history.scrollTop = history.scrollHeight;
        }
        if (d.refresh_dashboard && typeof updateDashboardData === 'function') updateDashboardData();
    });
}

async function runSimulation() {
    const btn = Array.from(document.querySelectorAll('#simulate .btn-start')).find(b => b.textContent.includes('Execute'));
    const statBadge = document.getElementById('sim-status');
    if (btn) { btn.textContent = "⏳ Simulating..."; btn.disabled = true; }
    if (statBadge) { statBadge.textContent = "CALCULATING"; statBadge.className = "badge RUNNING"; }

    if (typeof getSimParams !== 'function') return;
    const data = getSimParams();

    fetch('/simulate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }).then(r => r.json()).then(res => {
        if (btn) { btn.textContent = "▶ Execute Simulation (HMAC Seeded)"; btn.disabled = false; }
        if (res.error) {
            if (statBadge) { statBadge.textContent = "ERR: " + res.error; statBadge.className = "badge PAUSED"; }
            return;
        }
        if (statBadge) { statBadge.textContent = "COMPLETE"; statBadge.className = "badge RUNNING"; }

        const pnl = res.final_balance - res.starting_balance;
        const wr = res.wins / (res.wins + res.losses) * 100;

        const resBal = document.getElementById('res-bal');
        if (resBal) {
            resBal.textContent = `$${res.final_balance.toFixed(4)}`;
            resBal.style.color = pnl >= 0 ? 'var(--primary)' : 'var(--danger)';
        }
        const resPnl = document.getElementById('res-pnl');
        if (resPnl) {
            resPnl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(4)}`;
            resPnl.style.color = pnl >= 0 ? 'var(--primary)' : 'var(--danger)';
        }

        const resWager = document.getElementById('res-wager');
        if (resWager) resWager.textContent = `$${res.total_wagered.toFixed(2)}`;
        const resWr = document.getElementById('res-wr');
        if (resWr) resWr.textContent = `${wr.toFixed(2)}%`;
        const resDd = document.getElementById('res-dd');
        if (resDd) resDd.textContent = `${res.worst_drawdown.toFixed(2)}%`;
        const resPeak = document.getElementById('res-peak');
        if (resPeak) resPeak.textContent = `$${res.peak_balance.toFixed(4)}`;
        const resWl = document.getElementById('res-wl');
        if (resWl) resWl.textContent = `${res.wins} / ${res.losses}`;
        const resCb = document.getElementById('res-cb');
        if (resCb) resCb.textContent = res.circuit_breakers;

        const summary = document.getElementById('sim-summary');
        if (summary) {
            summary.style.display = 'block';
        }

        if (res.equity_curve && res.equity_curve.length > 1 && typeof renderSimChart === 'function') {
            renderSimChart(res, pnl);
        }
    });
}

function getSimParams() {
    const stratEl = document.getElementById('sim_strategy');
    if (!stratEl) return {};
    const strat = stratEl.value;
    const data = {
        strategy: strat,
        starting_balance: parseFloat(document.getElementById('sim_balance').value),
        bets_to_simulate: parseInt(document.getElementById('sim_bets').value),
        all_time_drawdown_cap_usd: parseFloat(document.getElementById('sim_atcap').value),
        min_bet_floor: parseFloat(document.getElementById('sim_floor').value)
    };

    const mappings = {
        the_gork: { bg: 's_g_base', tp: 's_g_tp', sl: 's_g_sl' },
        martingale: { bg: 's_martingale_base_usd', mult: 's_martingale_mult', max: 's_martingale_max_usd' },
        dalembert: { bg: 's_dalembert_base_usd' },
        labouchere: { bg: 's_labouchere_base_usd' }
    };

    const m = mappings[strat];
    if (m) {
        Object.keys(m).forEach(key => {
            const inputId = m[key];
            const el = document.getElementById(inputId);
            if (el) {
                const val = parseFloat(el.value);
                if (key === 'bg') {
                    if (strat === 'the_gork') data[strat + '_base_usd'] = val;
                    else data[strat + '_base_usd'] = val;
                }
                else if (key === 'tp') data[strat + '_tp_usd'] = val;
                else if (key === 'sl') data[strat + '_sl_usd'] = val;
                else if (key === 'mult') data[strat + '_mult'] = val;
                else if (key === 'max') data[strat + '_max_usd'] = val;
            }
        });
    } else if (strat === 'custom') {
        const s_c_dl = document.getElementById('s_c_daily');
        if (s_c_dl) data.c_daily = parseFloat(s_c_dl.value);
        document.querySelectorAll('#sim_dynamic_custom_params .dynamic-param-input').forEach(input => {
            const key = input.id.replace('s_dyn_', '');
            data[key] = input.type === 'number' ? parseFloat(input.value) : input.value;
        });
    } else if (strat === 'basic') {
        const s_amt = document.getElementById('s_basic_bet_amount');
        if (s_amt) data.basic_bet_amount = parseFloat(s_amt.value);
        const s_tg = document.getElementById('s_basic_target');
        if (s_tg) data.basic_target = parseFloat(s_tg.value);
        const s_cond = document.getElementById('s_basic_condition');
        if (s_cond) data.basic_condition = s_cond.value;
    }
    return data;
}

// Keno grid initialization
function initKenoGrid() {
    const grid = document.getElementById('keno-grid');
    if (!grid || grid.children.length > 0) return;
    for (let i = 1; i <= 40; i++) {
        const btn = document.createElement('div');
        btn.textContent = i;
        btn.dataset.num = i;
        btn.className = 'keno-num';
        btn.style.cssText = 'cursor:pointer; text-align:center; padding:6px 2px; border-radius:6px; font-size:0.75rem; font-weight:600; background:rgba(255,255,255,0.05); border:1px solid var(--border); color:var(--secondary); transition:all 0.2s;';
        btn.addEventListener('click', () => {
            const selected = grid.querySelectorAll('.keno-num.selected');
            if (btn.classList.contains('selected')) {
                btn.classList.remove('selected');
                btn.style.background = 'rgba(255,255,255,0.05)';
                btn.style.color = 'var(--secondary)';
                btn.style.borderColor = 'var(--border)';
            } else if (selected.length < 10) {
                btn.classList.add('selected');
                btn.style.background = 'rgba(255,71,87,0.2)';
                btn.style.color = 'var(--danger)';
                btn.style.borderColor = 'var(--danger)';
            }
        });
        grid.appendChild(btn);
    }
}

document.addEventListener('DOMContentLoaded', initKenoGrid);
