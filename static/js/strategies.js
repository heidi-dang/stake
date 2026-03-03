// Strategy configuration and simulator logic
const STRAT_DESCS = {
    the_gork: "Auto-scales bet size based on distance to the daily starting bankroll target. The ultimate distance recovery algorithm.",
    ema_cross: "Trend following algorithm calculating the 5-Period and 20-Period Exponential Moving Average of float roll probabilities. Buys >50.50 dynamically.",
    die_last: "Aggressive progression. Higher base bet, utilizes 2.5x streak multipliers. Can drop base bet by 50% on prolonged loss streaks.",
    vanish_in_volume: "Ultra-defensive. Maximum 1.8x streak multiplier. Automatically shrinks base bet sizes the further the session drops into drawdown.",
    eternal_volume: "Flat fractional sizing every single bet. Highest volume, lowest ruin rate.",
    reverted_martingale: "Shrinks your exposure on bad luck instead of doubling down. Lowest bankruptcy probability on the market.",
    wager_grind_99: "Strict 99% odds flat bet engine used exclusively to accumulate Stake wager volume safely.",
    custom: "Your custom Python strategy. Modifiable in the Editor tab.",
    basic: "Simple absolute-bet strategy with win/loss multipliers. Perfect for Martingale or fixed-size volume.",
    fibonacci: "Mathematical recovery algorithm. Follows the Fibonacci sequence (1,1,2,3,5...) on a loss to absorb catastrophic strings, and retreats 2 steps forward on a win.",
    paroli: "Reverse Martingale variation. Hunts for a win streak by doubling profit. Banks cash immediately upon hitting the Streak Target.",
    oscars_grind: "Target seeking algorithm. Aims to exactly win 1 base unit of profit per sequence, maintaining high stability against localized variance."
};

function toggleStrat() {
    const stratEl = document.getElementById('strategy');
    if (!stratEl) return;
    const strat = stratEl.value;

    document.querySelectorAll('.strat-panel').forEach(p => p.style.display = 'none');

    const desc = document.getElementById('strat-desc');
    const configId = (strat === 'ema_cross' ? 'ema_config' :
        strat === 'vanish_in_volume' ? 'vanish_config' :
            strat === 'eternal_volume' ? 'eternal_config' :
                strat + '_config');

    const panel = document.getElementById(configId);
    if (panel) panel.style.display = 'block';
    if (desc) desc.textContent = STRAT_DESCS[strat] || '';

    if (strat === 'custom') {
        renderDynamicParams('custom_config', 'dynamic_custom_params');
    } else if (strat === 'basic') {
        toggleBasicMults();
    }
}

function simToggleStrat() {
    const stratEl = document.getElementById('sim_strategy');
    if (!stratEl) return;
    const strat = stratEl.value;

    document.querySelectorAll('.sim-strat-panel').forEach(p => p.style.display = 'none');

    const map = {
        the_gork: 'sim_gork_config',
        basic: 'sim_basic_config',
        ema_cross: 'sim_ema_config',
        die_last: 'sim_die_last_config',
        vanish_in_volume: 'sim_vanish_config',
        eternal_volume: 'sim_eternal_config',
        reverted_martingale: 'sim_reverted_martingale_config',
        wager_grind_99: 'sim_wager_grind_99_config',
        custom: 'sim_custom_config'
    };

    if (map[strat]) document.getElementById(map[strat]).style.display = 'block';
    const desc = document.getElementById('sim-strat-desc');
    if (desc) desc.textContent = STRAT_DESCS[strat] || '';

    if (strat === 'custom') renderDynamicParams('sim_custom_config', 'sim_dynamic_custom_params', 's_');
}

function syncPctToUsd(baseId) {
    const isSim = baseId.startsWith('s_');
    const input = document.getElementById(baseId);
    if (!input) return;
    const pct = parseFloat(input.value) / 100;
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
    const usdEl = document.getElementById(baseId + '_usd');
    if (!usdEl) return;
    const usdVal = parseFloat(usdEl.value);
    const pctInput = document.getElementById(baseId);
    if (!pctInput || isNaN(usdVal)) return;

    const price = globalPrices[currentCurrency] || 1;
    const balance = isSim ? parseFloat(document.getElementById('sim_balance').value) : currentBalance;
    if (balance > 0 && price > 0) {
        pctInput.value = ((usdVal / price) / balance * 100).toFixed(8);
    }
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
    const condEl = document.getElementById(prefix + 'basic_condition');
    if (!condEl) return;
    const isOver = condEl.value === 'over';
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
    if (!container) return;
    container.innerHTML = '<div style="color:var(--secondary); font-size:0.75rem;">Syncing dynamic parameters...</div>';

    fetch('/strategy/params')
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
