const originalFetch = window.fetch;
window.fetch = async function() {
    let [resource, config] = arguments;
    if(!config) config = {};
    if(!config.headers) config.headers = {};
    const token = localStorage.getItem('gork_jwt');
    if(token) config.headers['Authorization'] = 'Bearer ' + token;
    const response = await originalFetch(resource, config);
    if(response.status === 401) {
        const overlay = document.getElementById('login-overlay');
        if (overlay) overlay.style.display = 'flex';
    }
    return response;
};

async function submitLogin() {
    const pwEl = document.getElementById('login-pw');
    if (!pwEl) return;
    const pw = pwEl.value;
    const r = await originalFetch('/login', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({password: pw})
    });
    if(r.ok) {
        const data = await r.json();
        localStorage.setItem('gork_jwt', data.token);
        const overlay = document.getElementById('login-overlay');
        if (overlay) overlay.style.display = 'none';
        if (typeof loadSettingsData === 'function') loadSettingsData();
    } else {
        const errEl = document.getElementById('login-err');
        if (errEl) errEl.style.display = 'block';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const token = localStorage.getItem('gork_jwt');
    const overlay = document.getElementById('login-overlay');
    if (overlay) {
        if(token) overlay.style.display = 'none';
        else setTimeout(() => { overlay.style.display = 'flex'; }, 100);
    }
});
