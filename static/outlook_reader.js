const API = '/outlook/api';

const el = (id) => document.getElementById(id);

function showError(message, details) {
    const box = el('fetch-error');
    let text = message || 'Errore sconosciuto.';
    if (details) {
        text += '\n' + (typeof details === 'string' ? details : JSON.stringify(details, null, 2));
    }
    box.textContent = text;
    box.hidden = false;
}

function clearError() {
    const box = el('fetch-error');
    box.textContent = '';
    box.hidden = true;
}

async function jsonFetch(url, options) {
    const response = await fetch(url, options);
    let payload = null;
    try {
        payload = await response.json();
    } catch (err) {
        payload = null;
    }
    if (!response.ok) {
        const message = (payload && (payload.error || payload.message)) || `HTTP ${response.status}`;
        throw Object.assign(new Error(message), { payload, status: response.status });
    }
    return payload;
}

function setStatusValue(id, value, ok) {
    const node = el(id);
    node.textContent = value;
    node.classList.remove('ok', 'ko');
    if (ok === true) node.classList.add('ok');
    if (ok === false) node.classList.add('ko');
}

async function loadStatus() {
    try {
        const data = await jsonFetch(`${API}/status`);
        setStatusValue('status-configured', data.configured ? 'YES' : 'NO', data.configured);
        setStatusValue('status-authenticated', data.authenticated ? 'YES' : 'NO', data.authenticated);
        setStatusValue('status-account', (data.account && (data.account.username || data.account.name)) || '-');
        setStatusValue('status-tenant', data.tenant_id || '-');
    } catch (err) {
        setStatusValue('status-configured', 'ERROR', false);
        showError(err.message);
    }
}

async function loadSettings() {
    try {
        const data = await jsonFetch(`${API}/settings`);
        const settings = data.settings;
        el('client_id').value = settings.client_id || '';
        el('tenant_id').value = settings.tenant_id || '';
        el('redirect_uri').value = settings.redirect_uri || '';
        el('scopes').value = (settings.scopes || []).join(', ');
        el('use_proxy').checked = !!settings.use_proxy;
        el('client_secret').value = '';
        el('client_secret').placeholder = settings.has_client_secret
            ? 'valore salvato — lascia vuoto per mantenerlo'
            : 'inserisci il client secret';
        el('aliases').value = Object.entries(settings.aliases || {})
            .map(([alias, target]) => `${alias}=${target}`)
            .join(', ');
        el('api_token').value = '';
        el('clear_api_token').checked = false;
        el('api_token').placeholder = settings.has_api_token
            ? 'token configurato — lascia vuoto per mantenerlo'
            : 'opzionale: protegge /outlook/api/* per gli agenti';
        if (!data.status.configured) {
            el('settings-details').open = true;
        }
    } catch (err) {
        showError(err.message);
    }
}

async function saveSettings(event) {
    event.preventDefault();
    clearError();
    const payload = {
        client_id: el('client_id').value.trim(),
        tenant_id: el('tenant_id').value.trim(),
        redirect_uri: el('redirect_uri').value.trim(),
        scopes: el('scopes').value,
        aliases: el('aliases').value,
        use_proxy: el('use_proxy').checked,
    };
    const secret = el('client_secret').value.trim();
    if (secret) payload.client_secret = secret;
    if (el('clear_api_token').checked) {
        payload.api_token = '';
    } else {
        const apiToken = el('api_token').value.trim();
        if (apiToken) payload.api_token = apiToken;
    }
    try {
        await jsonFetch(`${API}/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        el('client_secret').value = '';
        await loadStatus();
        await loadSettings();
        renderCurl();
    } catch (err) {
        showError(err.message, err.payload && err.payload.details);
    }
}

async function loadFolders() {
    clearError();
    const params = new URLSearchParams();
    const mailbox = el('mailbox-folders').value.trim();
    if (mailbox) params.set('mailbox', mailbox);
    if (el('include_hidden').checked) params.set('include_hidden', 'true');
    const backend = el('backend-select').value;
    if (backend !== 'graph') params.set('backend', backend);

    const output = el('folders-output');
    output.textContent = 'Loading...';
    try {
        const data = await jsonFetch(`${API}/folders?${params.toString()}`);
        output.innerHTML = '';
        if (!data.folders.length) {
            output.textContent = 'Nessuna cartella restituita.';
            return;
        }
        const foldersInput = el('folders');
        data.folders.forEach((folder) => {
            const chip = document.createElement('span');
            chip.className = 'folder-chip';
            chip.textContent = folder.path;
            const count = document.createElement('span');
            count.className = 'count';
            count.textContent = folder.totalItemCount != null ? `(${folder.totalItemCount})` : '';
            chip.appendChild(count);
            chip.title = folder.source === 'mailbox'
                ? `folder from the mailbox, id: ${folder.id}`
                : `id: ${folder.id}`;
            chip.addEventListener('click', () => {
                const current = foldersInput.value.split(',').map((v) => v.trim()).filter(Boolean);
                if (!current.includes(folder.path)) {
                    current.push(folder.path);
                    foldersInput.value = current.join(',');
                }
            });
            output.appendChild(chip);
        });
    } catch (err) {
        output.textContent = '';
        const details = err.payload && err.payload.details && err.payload.details.available_folders;
        showError(err.message, details ? { available_folders: details } : null);
    }
}

function formatDate(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function addressOf(entry) {
    if (!entry) return '';
    if (entry.emailAddress) {
        const name = entry.emailAddress.name;
        const address = entry.emailAddress.address || '';
        if (!name || name === address) return address;
        return `${name} <${address}>`;
    }
    return entry.address || '';
}

function renderMessages(data) {
    const output = el('messages-output');
    output.innerHTML = '';

    const summary = document.createElement('p');
    summary.className = 'hint';
    const folderList = (data.folders || []).map((f) => f.requested).join(', ') || 'all';
    summary.textContent = `[+] mailbox=${data.mailbox} folders=${folderList} count=${data.count}`;
    output.appendChild(summary);

    (data.messages || []).forEach((message) => {
        const card = document.createElement('div');
        card.className = 'message-card' + (message.isRead ? '' : ' unread');

        const header = document.createElement('div');
        header.className = 'message-header';

        const subject = document.createElement('span');
        subject.className = 'message-subject';
        subject.textContent = message.subject || '(no subject)';

        const date = document.createElement('span');
        date.className = 'message-date';
        date.textContent = formatDate(message.receivedDateTime);

        header.appendChild(subject);
        header.appendChild(date);
        card.appendChild(header);

        const meta = document.createElement('div');
        meta.className = 'message-meta';
        const to = (message.toRecipients || []).map(addressOf).join('; ');
        meta.textContent = `FROM: ${addressOf(message.from)}${to ? `  |  TO: ${to}` : ''}`;
        card.appendChild(meta);

        if (message.bodyPreview) {
            const preview = document.createElement('div');
            preview.className = 'message-preview';
            preview.textContent = message.bodyPreview;
            card.appendChild(preview);
        }

        const actions = document.createElement('div');
        actions.className = 'message-actions';

        const detailBtn = document.createElement('button');
        detailBtn.type = 'button';
        detailBtn.textContent = 'LOAD_FULL_MESSAGE';
        detailBtn.addEventListener('click', () => loadMessageDetail(message.id, card, detailBtn));
        actions.appendChild(detailBtn);

        if (message.hasAttachments) {
            const flag = document.createElement('span');
            flag.style.color = 'var(--neon-yellow)';
            flag.textContent = '[*] ATTACHMENTS';
            actions.appendChild(flag);
        }

        if (message.webLink) {
            const link = document.createElement('a');
            link.href = message.webLink;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = 'OPEN_IN_OUTLOOK';
            actions.appendChild(link);
        }

        card.appendChild(actions);
        output.appendChild(card);
    });
}

async function loadMessageDetail(messageId, card, button) {
    clearError();

    const existing = card.querySelector('.message-body');
    if (existing) {
        existing.remove();
        button.textContent = 'LOAD_FULL_MESSAGE';
        return;
    }

    button.disabled = true;
    button.textContent = 'LOADING...';
    try {
        const params = new URLSearchParams();
        const mailbox = el('mailbox').value.trim();
        if (mailbox) params.set('mailbox', mailbox);
        params.set('body_type', el('body_type').value);
        const backend = el('backend-select').value;
        if (backend !== 'graph') params.set('backend', backend);
        const message = await jsonFetch(`${API}/messages/${encodeURIComponent(messageId)}?${params.toString()}`);

        const body = document.createElement('div');
        body.className = 'message-body';
        const raw = (message.body && message.body.content) || '';
        body.textContent = message.body && message.body.contentType === 'html'
            ? raw.replace(/<[^>]+>/g, ' ')
            : raw || '(empty body)';
        card.appendChild(body);
        button.textContent = 'HIDE_FULL_MESSAGE';
    } catch (err) {
        showError(err.message, err.payload && err.payload.details);
        button.textContent = 'LOAD_FULL_MESSAGE';
    } finally {
        button.disabled = false;
    }
}

const CHECKBOX_PARAMS = ['unread', 'has_attachments'];

async function fetchMessages(event) {
    event.preventDefault();
    clearError();
    el('messages-output').innerHTML = '';

    const params = new URLSearchParams();
    ['mailbox', 'folders', 'top', 'from', 'subject', 'search', 'since', 'until', 'orderby', 'body_type', 'filter']
        .forEach((name) => {
            const node = el(name);
            if (!node) return;
            const value = node.value.trim();
            if (value) params.set(name, value);
        });

    CHECKBOX_PARAMS.forEach((name) => {
        const node = el(name);
        if (node && node.checked) params.set(name, 'true');
    });

    const backend = el('backend-select').value;
    if (backend !== 'graph') params.set('backend', backend);

    // $filter/$search conflict only applies to the Graph backend.
    if (backend === 'graph' && params.get('search')
        && (params.get('filter') || params.get('from') || params.get('subject')
            || params.get('since') || params.get('until'))) {
        showError('Microsoft Graph non consente $filter insieme a $search. Usa solo la ricerca KQL oppure solo i filtri.');
        return;
    }

    el('loading').hidden = false;
    try {
        const data = await jsonFetch(`${API}/messages?${params.toString()}`);
        renderMessages(data);
    } catch (err) {
        const details = err.payload && err.payload.details;
        showError(err.message, details ? (details.available_folders || details) : null);
    } finally {
        el('loading').hidden = true;
    }
}

function showOwaOutput(payload) {
    const node = el('owa-output');
    node.hidden = false;
    node.textContent = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2);
}

async function loadOwaStatus() {
    try {
        const data = await jsonFetch(`${API}/owa/status`);
        setStatusValue('owa-playwright', data.playwright_installed ? 'YES' : 'NO', data.playwright_installed);
        setStatusValue('owa-profile',
            data.profile_signed_in ? 'SIGNED_IN' : 'NEEDS_LOGIN',
            data.profile_signed_in);
        setStatusValue('owa-active', activeBackend().toUpperCase(), true);
    } catch (err) {
        setStatusValue('owa-playwright', 'ERROR', false);
    }
}

// ---------------------------------------------------------------------
// Auth tabs: the active tab also selects the backend for sections 2 and 3.
// ---------------------------------------------------------------------
function activeBackend() {
    const select = el('backend-select');
    return select ? select.value : 'graph';
}

function selectAuthTab(backend) {
    const target = backend === 'owa' ? 'owa' : 'graph';
    const select = el('backend-select');
    if (select) select.value = target;

    document.querySelectorAll('.auth-tab').forEach((tab) => {
        const isActive = tab.dataset.backend === target;
        tab.classList.toggle('is-active', isActive);
        tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    document.querySelectorAll('.tab-pane').forEach((pane) => {
        pane.hidden = pane.id !== `pane-${target}`;
    });

    const active = el('owa-active');
    if (active) {
        active.textContent = target.toUpperCase();
        active.classList.remove('ok', 'ko');
        active.classList.add(target === 'owa' ? 'ko' : 'ok');
    }
    renderCurl();
    try {
        window.localStorage.setItem('outlook.authTab', target);
    } catch (err) {
        /* storage may be disabled; the tab still works for this page load */
    }
}

let owaBootstrapPoll = null;

function stopBootstrapPolling() {
    if (owaBootstrapPoll) {
        clearInterval(owaBootstrapPoll);
        owaBootstrapPoll = null;
    }
}

async function bootstrapOwa() {
    clearError();
    const button = el('btn-owa-bootstrap');
    button.disabled = true;
    button.textContent = 'WAITING_FOR_LOGIN...';
    showOwaOutput('Finestra in apertura: completa il login Microsoft (anche MFA). '
        + 'La finestra resta aperta finche\' la casella non e\' raggiungibile.');

    try {
        showOwaOutput(await jsonFetch(`${API}/owa/bootstrap`, { method: 'POST' }));
    } catch (err) {
        showOwaOutput(err.payload || { error: err.message });
        button.disabled = false;
        button.textContent = 'BOOTSTRAP_OWA_LOGIN';
        return;
    }

    stopBootstrapPolling();
    let attempts = 0;
    owaBootstrapPoll = setInterval(async () => {
        attempts += 1;
        let status = null;
        try {
            status = await jsonFetch(`${API}/owa/bootstrap/status`);
        } catch (err) {
            status = null;
        }
        if (status) {
            showOwaOutput(status);
            if (status.authenticated) {
                stopBootstrapPolling();
                button.disabled = false;
                button.textContent = 'BOOTSTRAP_OWA_LOGIN';
                clearError();
                await loadOwaStatus();
                return;
            }
            if (!status.running) {
                stopBootstrapPolling();
                button.disabled = false;
                button.textContent = 'BOOTSTRAP_OWA_LOGIN';
                showError(status.message || 'Bootstrap concluso senza login.');
                await loadOwaStatus();
                return;
            }
        }
        // Safety net only: bootstrap itself enforces the real budget.
        if (attempts > 900) {
            stopBootstrapPolling();
            button.disabled = false;
            button.textContent = 'BOOTSTRAP_OWA_LOGIN';
        }
    }, 2000);
}

async function owaDiagnostics() {
    clearError();
    const button = el('btn-owa-diag');
    button.disabled = true;
    button.textContent = 'PROBING...';
    try {
        showOwaOutput(await jsonFetch(`${API}/owa/diagnostics`));
    } catch (err) {
        showOwaOutput(err.payload || { error: err.message });
    } finally {
        button.disabled = false;
        button.textContent = 'DIAGNOSTICS';
    }
}

async function verifyIdentity() {
    clearError();
    try {
        const me = await jsonFetch(`${API}/me`);
        setStatusValue('status-account', me.mail || me.userPrincipalName || me.displayName || '-');
    } catch (err) {
        showError(err.message, err.payload && err.payload.details);
    }
}

async function logout() {
    clearError();
    try {
        await jsonFetch(`${API}/logout`, { method: 'POST' });
        await loadStatus();
    } catch (err) {
        showError(err.message);
    }
}

function renderCurl() {
    const origin = window.location.origin;
    const base = `${origin}${API}`;
    const token = el('api_token').value.trim();
    const header = token ? ` \\\n  -H "X-API-Token: ${token}"` : '';
    const isOwa = activeBackend() === 'owa';
    const backendParam = isOwa ? `  --data-urlencode "backend=owa" \\\n` : '';
    const backendQuery = isOwa ? '&backend=owa' : '';

    const lines = [
        `# Stato connessione`,
        `curl -s "${base}/status"${header}`,
        ``,
        `# Cartelle (ricorsive)${isOwa ? ' — backend OWA, include le cartelle custom' : ' di una casella/alias'}`,
        `curl -s "${base}/folders?mailbox=team@azienda.it${backendQuery}"${header}`,
        ``,
        `# Messaggi da piu' cartelle`,
        `curl -s -G "${base}/messages"${header} \\`,
        backendParam + `  --data-urlencode "mailbox=team@azienda.it" \\`,
        `  --data-urlencode "folders=Inbox,Sent Items,Projects/Client" \\`,
        `  --data-urlencode "from=mario@example.com" \\`,
        `  --data-urlencode "since=2026-01-01T00:00:00Z" \\`,
        `  --data-urlencode "top=50"`,
        ``,
        `# Ricerca testuale`,
        `curl -s -G "${base}/messages"${header} \\`,
        backendParam + `  --data-urlencode "mailbox=team@azienda.it" \\`,
        `  --data-urlencode 'search=from:"mario@example.com" subject:"fattura"' \\`,
        `  --data-urlencode "top=25"`,
        ``,
        `# Dettaglio completo di un messaggio`,
        `curl -s "${base}/messages/AAMkAD...?mailbox=team@azienda.it&body_type=text${backendQuery}"${header}`,
    ];
    el('curl-snippet').textContent = lines.join('\n');
}

let loginWindow = null;
let loginPoll = null;

function setLoginButtonBusy(busy) {
    const button = el('btn-login');
    if (!button) return;
    button.disabled = busy;
    button.textContent = busy ? 'WAITING_FOR_MICROSOFT...' : 'LOGIN_WITH_MICROSOFT';
}

function stopLoginPolling() {
    if (loginPoll) {
        clearInterval(loginPoll);
        loginPoll = null;
    }
}

function closeLoginPopup() {
    if (loginWindow) {
        try {
            if (!loginWindow.closed) loginWindow.close();
        } catch (err) {
            /* Cross-Origin-Opener-Policy may hide .closed; closing is best effort */
        }
        loginWindow = null;
    }
}

function makeGeneration() {
    // 32 chars from [A-Za-z0-9_-]; valid correlation id and OAuth state.
    const bytes = new Uint8Array(24);
    if (window.crypto && window.crypto.getRandomValues) {
        window.crypto.getRandomValues(bytes);
    } else {
        for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
    }
    return btoa(String.fromCharCode.apply(null, bytes))
        .replace(/\+/g, '-')
        .replace(/\//g, '_')
        .replace(/=+$/, '');
}

/**
 * Microsoft sends Cross-Origin-Opener-Policy, which severs the popup from this
 * page: window.closed, window.location and postMessage are all unreliable. So
 * the popup is opened synchronously (inside the click, or the blocker rejects
 * it) straight at our own login route, and completion is detected by polling
 * the server, where the callback records the outcome for this generation.
 */
function openLoginPopup() {
    clearError();
    const width = 520;
    const height = 700;
    const left = Math.max(0, (window.screenX || 0) + ((window.outerWidth || width) - width) / 2);
    const top = Math.max(0, (window.screenY || 0) + ((window.outerHeight || height) - height) / 2);
    const generation = makeGeneration();

    // Same-origin page first: keeps this call inside the user gesture.
    loginWindow = window.open(
        'about:blank',
        `outlook-oauth-${generation}`,
        `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no`
    );
    const startUrl = `${API.replace(/\/api$/, '')}/oauth/login?generation=${encodeURIComponent(generation)}`;
    if (!loginWindow) {
        // Popup blocked: continue in this tab, which always works.
        window.location.href = startUrl;
        return;
    }

    setLoginButtonBusy(true);
    stopLoginPolling();

    try {
        loginWindow.location.replace(startUrl);
    } catch (err) {
        // If navigation was blocked, fall back to the same-tab flow.
        closeLoginPopup();
        setLoginButtonBusy(false);
        window.location.href = startUrl;
        return;
    }

    pollLoginOutcome(generation);
}

function pollLoginOutcome(generation) {
    let attempts = 0;
    const maxAttempts = 300; // ~5 minutes, the usual auth window
    loginPoll = setInterval(async () => {
        attempts += 1;
        let status = null;
        try {
            status = await jsonFetch(`${API}/status?generation=${encodeURIComponent(generation)}`);
        } catch (err) {
            status = null;
        }

        const outcome = status && status.auth_outcome;
        // Finished once the server recorded a result for this attempt.
        const answered = outcome && !outcome.newer
            && outcome.success !== null && outcome.success !== undefined;

        if (answered) {
            stopLoginPolling();
            setLoginButtonBusy(false);
            closeLoginPopup();
            if (outcome.success) {
                clearError();
                await loadStatus();
                await loadSettings();
            } else {
                showError(outcome.message || 'Autenticazione non riuscita.');
                await loadStatus();
            }
            return;
        }

        // COOP makes .closed throw or lie; only an explicit true counts, and
        // give the popup a few seconds to navigate before trusting it.
        let closed = false;
        try {
            closed = loginWindow ? loginWindow.closed === true : false;
        } catch (err) {
            closed = false;
        }

        if (closed && attempts > 5) {
            stopLoginPolling();
            loginWindow = null;
            setLoginButtonBusy(false);
            showError("Finestra di login chiusa senza completare l'autenticazione. Riprova.");
        } else if (attempts >= maxAttempts) {
            stopLoginPolling();
            loginWindow = null;
            setLoginButtonBusy(false);
            showError('Login non completato entro il tempo previsto. Riprova.');
        }
    }, 1000);
}

function handleCallbackMessage() {
    const params = new URLSearchParams(window.location.search);
    const status = params.get('status');
    const message = params.get('message');
    if (!status) return;
    if (status === 'success') {
        setStatusValue('status-authenticated', 'YES', true);
        loadStatus();
    } else {
        showError(message || 'Autenticazione non riuscita.');
    }
    window.history.replaceState({}, document.title, window.location.pathname);
}

document.addEventListener('DOMContentLoaded', () => {
    el('settings-form').addEventListener('submit', saveSettings);
    el('fetch-form').addEventListener('submit', fetchMessages);
    el('btn-folders').addEventListener('click', loadFolders);
    el('btn-login').addEventListener('click', openLoginPopup);
    el('btn-logout').addEventListener('click', logout);
    el('btn-me').addEventListener('click', verifyIdentity);
    el('btn-curl').addEventListener('click', renderCurl);
    el('btn-owa-bootstrap').addEventListener('click', bootstrapOwa);
    el('btn-owa-diag').addEventListener('click', owaDiagnostics);

    document.querySelectorAll('.auth-tab').forEach((tab) => {
        tab.addEventListener('click', () => selectAuthTab(tab.dataset.backend));
    });
    let savedTab = 'graph';
    try {
        savedTab = window.localStorage.getItem('outlook.authTab') || 'graph';
    } catch (err) {
        savedTab = 'graph';
    }
    selectAuthTab(savedTab);

    loadStatus().then(loadSettings).then(renderCurl);
    loadOwaStatus();
    handleCallbackMessage();
});
