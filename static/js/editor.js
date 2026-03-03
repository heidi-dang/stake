// Strategy Presets and Editor Logic
let editor;

function initEditor() {
    const el = document.getElementById("ace-editor");
    if (!el) {
        console.warn('Ace editor container #ace-editor not found. Will retry on tab switch.');
        return;
    }
    try {
        editor = ace.edit("ace-editor");
        editor.setTheme("ace/theme/tomorrow_night_eighties");
        editor.session.setMode("ace/mode/python");
        editor.setOptions({ fontSize: "14px" });
    } catch (e) {
        console.warn('Ace editor init error:', e);
    }
}

function saveCustomCode() {
    if (!editor) return;
    const code = editor.getValue();
    const status = document.getElementById('editor-status');
    if (status) { status.textContent = "Saving..."; status.style.color = "var(--secondary)"; }

    fetch('/custom_strategy', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
    }).then(r => r.json()).then(d => {
        if (d.success) {
            if (status) { status.textContent = "✓ Saved successfully."; status.style.color = "var(--primary)"; }
            setTimeout(() => { if (status) status.textContent = ""; }, 3000);
        } else {
            if (status) { status.textContent = "Error: " + d.error; status.style.color = "var(--danger)"; }
        }
    });
}

function loadCustomCode() {
    fetch('/custom_strategy').then(r => r.json()).then(d => {
        if (d.code && editor) editor.setValue(d.code, -1);
    });
}

function fetchTemplates() {
    fetch('/strategy_templates').then(r => r.json()).then(list => {
        const sel = document.getElementById('template-select');
        if (sel) sel.innerHTML = list.map(t => `<option value="${t}">${t.replace(/_/g, ' ').toUpperCase()}</option>`).join('');
    });
}

function loadTemplate() {
    const sel = document.getElementById('template-select');
    if (!sel) return;
    const name = sel.value;
    if (!confirm('This will overwrite your current editor code. Proceed?')) return;
    fetch('/strategy_templates/' + name).then(r => r.json()).then(d => {
        if (d.code && editor) editor.setValue(d.code, -1);
    });
}

function loadPresets() {
    fetch('/strategies').then(r => r.json()).then(list => {
        const container = document.getElementById('preset-list');
        if (!container) return;
        if (!list.length) { container.innerHTML = '<div style="color:var(--secondary);font-size:0.85rem;">No saved presets yet.</div>'; return; }
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
    fetch('/strategies/' + id).then(r => r.json()).then(p => {
        if (!p.config) return;
        const cfg = p.config;
        const simStrat = document.getElementById('sim_strategy');
        if (simStrat) {
            simStrat.value = p.strategy;
            simToggleStrat();
        }

        const atcap = document.getElementById('sim_atcap');
        if (atcap) atcap.value = cfg.all_time_drawdown_cap_pct ?? -8;
        const floor = document.getElementById('sim_floor');
        if (floor) floor.value = cfg.min_bet_floor ?? 0.000001;
        const bal = document.getElementById('sim_balance');
        if (cfg.starting_balance && bal) bal.value = cfg.starting_balance;

        const map = {
            base_bet_pct: 's_g_base',
            session_tp_pct: 's_g_tp',
            session_sl_pct: 's_g_sl',
            daily_loss_cap_pct: 's_g_daily',
            die_last_base_bet_pct: 's_dl_base',
            die_last_tp_pct: 's_dl_tp',
            die_last_sl_pct: 's_dl_sl',
            die_last_daily_loss_cap_pct: 's_dl_daily',
            vanish_base_bet_pct: 's_v_base',
            vanish_tp_pct: 's_v_tp',
            vanish_sl_pct: 's_v_sl',
            vanish_daily_loss_cap_pct: 's_v_daily',
            eternal_base_bet_pct: 's_e_base',
            eternal_tp_pct: 's_e_tp',
            eternal_sl_pct: 's_e_sl',
            eternal_daily_loss_cap_pct: 's_e_daily',
            ema_base_bet_pct: 's_ema_base'
        };

        Object.entries(map).forEach(([k, v]) => {
            const el = document.getElementById(v);
            if (cfg[k] !== undefined && el) el.value = cfg[k];
        });
    });
}

function savePreset() {
    const nameEl = document.getElementById('preset_name');
    if (!nameEl) return;
    const name = nameEl.value.trim();
    if (!name) { alert('Enter a preset name first.'); return; }
    if (typeof getSimParams !== 'function') return;
    const params = getSimParams();
    fetch('/strategies', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, strategy: params.strategy, config: params })
    }).then(r => r.json()).then(d => {
        if (d.success) { nameEl.value = ''; loadPresets(); }
        else alert('Error saving: ' + (d.error || 'unknown'));
    });
}

document.addEventListener('DOMContentLoaded', () => {
    initEditor();
});
