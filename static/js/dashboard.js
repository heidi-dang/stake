// Main dashboard UI logic
const gridOptions = {
    cellHeight: 64,
    margin: 8,
    minRow: 1,
    float: true,
    animate: true,
    handle: '.gs-drag-handle',
    resizable: { handles: 's, e, se' }
};

let currentBalance = 0;
let currentCurrency = 'btc';
let globalPrices = { btc: 100000, ltc: 100, eth: 2500 };

function openTab(btn, tabId) {
    try {
        document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

        btn.classList.add('active');
        const tabContent = document.getElementById(tabId);
        if (!tabContent) { console.warn('Tab not found:', tabId); return; }
        tabContent.classList.add('active');

        if (tabId === 'editor') {
            if (!editor && typeof initEditor === 'function') initEditor();
            try { if (editor) setTimeout(() => editor.resize(), 100); } catch (e) { }
        }

        try {
            const currentTab = document.getElementById(tabId);
            if (currentTab && typeof GridStack !== 'undefined') {
                const grids = currentTab.querySelectorAll('.grid-stack');
                grids.forEach(gridEl => {
                    if (!gridEl.gridstack) {
                        gridEl.gridstack = GridStack.init(gridOptions, gridEl);
                    } else {
                        setTimeout(() => {
                            try {
                                gridEl.gridstack.onParentResize();
                                gridEl.gridstack.compact();
                            } catch (e) { }
                        }, 50);
                    }
                });
            }
        } catch (gsErr) { console.warn('GridStack error:', gsErr); }
    } catch (err) {
        console.error('openTab error:', err);
    }
}

function updateDashboardData() {
    fetch('/status').then(r => r.json()).then(d => {
        currentBalance = d.balance.available;
        currentCurrency = d.balance.currency.toLowerCase();
        if (d.prices) globalPrices = d.prices;

        const balEl = document.getElementById('balance');
        if (balEl) balEl.textContent = d.balance.available.toFixed(8);
        const curEl = document.getElementById('currency');
        if (curEl) curEl.textContent = d.balance.currency.toUpperCase();
        const betEl = document.getElementById('bet');
        if (betEl) betEl.textContent = d.current_bet.toFixed(8);
        const betsEl = document.getElementById('bets');
        if (betsEl) betsEl.textContent = d.total_bets;
        const wagEl = document.getElementById('wagered');
        if (wagEl) wagEl.textContent = d.total_wagered.toFixed(8);

        const sb = document.getElementById('status-badge');
        const startBtn = document.getElementById('startBtn');
        if (sb) {
            if (d.is_running) {
                sb.textContent = 'RUNNING'; sb.className = 'badge RUNNING';
                if (startBtn) startBtn.classList.add('running-pulse');
            } else {
                sb.textContent = 'PAUSED'; sb.className = 'badge PAUSED';
                if (startBtn) startBtn.classList.remove('running-pulse');
            }
        }

        const logsDiv = document.getElementById('logs');
        if (logsDiv && d.logs) {
            logsDiv.innerHTML = d.logs.map(l => `<div>${l}</div>`).join('');
            logsDiv.scrollTop = logsDiv.scrollHeight;
        }

        // Sync seeds and nonces
        const seedSync = {
            'dt_server_seed': d.server_seed_hash,
            'dt_client_seed': d.client_seed,
            'dt_nonce': d.nonce,
            'dice_pred_server': d.server_seed_hash,
            'dice_pred_client': d.client_seed,
            'dice_pred_nonce': d.nonce
        };

        Object.keys(seedSync).forEach(id => {
            const el = document.getElementById(id);
            if (el && (!el.value || d.is_running)) {
                el.value = seedSync[id];
            }
        });

        if (d.is_running && document.getElementById('dragon').classList.contains('active')) {
            if (typeof predictDragon === 'function') predictDragon();
        }
    }).catch(e => console.warn("Status update error:", e));
}

function startBot() {
    const stratEl = document.getElementById('strategy');
    if (!stratEl) return;
    const strat = stratEl.value;

    // Global parameters - optional, fallback to defaults
    const at_cap_el = document.getElementById('all_time_cap');
    const min_bet_el = document.getElementById('min_bet');

    const data = {
        strategy: strat,
        all_time_drawdown_cap_pct: at_cap_el ? parseFloat(at_cap_el.value) : -8.0,
        min_bet_floor: min_bet_el ? parseFloat(min_bet_el.value) : 0.000001
    };

    // Strategy specific mapping
    const mappings = {
        the_gork: { bg: 'g_base', tp: 'g_tp', sl: 'g_sl', dl: 'g_daily' },
        martingale: { bg: 'martingale_base_usd', mult: 'martingale_mult', max: 'martingale_max_usd' },
        dalembert: { bg: 'dalembert_base_usd' },
        labouchere: { bg: 'labouchere_base_usd' }
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
                else if (key === 'dl') data[strat + '_daily_loss_cap_usd'] = val;
                else if (key === 'mult') data[strat + '_mult'] = val;
                else if (key === 'max') data[strat + '_max_usd'] = val;
            }
        });
    } else if (strat === 'custom') {
        const c_dl_el = document.getElementById('c_daily');
        if (c_dl_el) data.c_daily = parseFloat(c_dl_el.value);
        document.querySelectorAll('#dynamic_custom_params .dynamic-param-input').forEach(input => {
            const key = input.id.replace('dyn_', '');
            data[key] = input.type === 'number' ? parseFloat(input.value) : input.value;
        });
    } else if (strat === 'basic') {
        data.basic_bet_amount = parseFloat(document.getElementById('basic_bet_amount').value);
        data.basic_target = parseFloat(document.getElementById('basic_target').value);
        data.basic_condition = document.getElementById('basic_condition').value;
    }

    fetch('/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': localStorage.getItem('gork_token') },
        body: JSON.stringify(data)
    }).then(r => r.json()).then(d => {
        if (!d.success) console.error("Engine start failed:", d.error);
    });
}

function stopBot() { fetch('/stop', { method: 'POST' }); }

function loadSettingsData() {
    fetch('/status').then(r => r.json()).then(d => {
        if (d.config) {
            const gk = document.getElementById('gemini_key');
            if (gk) gk.value = d.config.gemini_api_key || '';
        }
    });
    fetch('/settings/vip').then(r => r.json()).then(d => {
        if (d.success) {
            const rank = document.getElementById('vip-rank');
            if (rank) rank.textContent = d.vipLevel || 'None';
            const pct = document.getElementById('vip-pct');
            if (pct) pct.textContent = (d.wagerProgress || 0).toFixed(2) + '%';
            const fill = document.getElementById('vip-fill');
            if (fill) fill.style.width = (d.wagerProgress || 0) + '%';
        }
    });
    fetch('/settings/wallets').then(r => r.json()).then(d => {
        if (d.success && d.wallets) { renderWallets(d.wallets); }
    });
}

function renderWallets(wallets) {
    const list = document.getElementById('wallet-list');
    if (!list) return;
    list.innerHTML = '';
    if (!wallets || wallets.length === 0) { list.innerHTML = '<div>No balances found.</div>'; return; }

    wallets.forEach(w => {
        const checked = w.currency === currentCurrency ? 'checked' : '';
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

function setWallet(currency) {
    fetch('/settings/set_wallet', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ currency })
    }).then(r => r.json()).then(d => {
        if (d.success) {
            currentCurrency = currency;
            const curEl = document.getElementById('currency');
            if (curEl) curEl.textContent = currency.toUpperCase();
        }
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
        headers: {
            'Content-Type': 'application/json',
            'Authorization': localStorage.getItem('gork_token')
        },
        body: JSON.stringify({ message: msg })
    }).then(r => r.json()).then(d => {
        if (d.error) {
            chat.innerHTML += `<div class="ai-msg ai-msg-bot" style="color:var(--danger);"><b>Error:</b> ${d.error}</div>`;
        } else {
            chat.innerHTML += `<div class="ai-msg ai-msg-bot"><b>Gemini:</b> ${d.reply}</div>`;
        }
        chat.scrollTop = chat.scrollHeight;
    }).catch(e => {
        chat.innerHTML += `<div class="ai-msg ai-msg-bot" style="color:var(--danger);"><b>System Error:</b> Could not reach AI backend.</div>`;
    });
}

function playManualGame(game) {
    // ... manual game logic (moved to games.js for better split)
}

// Initializations
document.addEventListener('DOMContentLoaded', () => {
    setInterval(updateDashboardData, 2000);
    setInterval(updateChart, 2000);

    if (typeof initMainChart === 'function') initMainChart();
    if (typeof toggleStrat === 'function') toggleStrat();
    if (typeof simToggleStrat === 'function') simToggleStrat();

    updateDashboardData();
    updateChart();
    loadSettingsData();
    if (typeof fetchTemplates === 'function') fetchTemplates();
    if (typeof loadPresets === 'function') loadPresets();
    if (typeof loadCustomCode === 'function') loadCustomCode();

    try {
        const terminalGrid = document.getElementById('gs-terminal');
        if (terminalGrid && typeof GridStack !== 'undefined') {
            terminalGrid.gridstack = GridStack.init(gridOptions, terminalGrid);
        }
    } catch (e) { console.warn('GridStack init error:', e); }
});
