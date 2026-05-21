/**
 * PTLifeEasy - Reverse Shell Multi-Handler Dashboard
 * Handles listeners, sessions, terminal I/O, payload generation, and shortcuts.
 */

// ─── State ──────────────────────────────────────────────────────
const RS = {
    activeSessionId: null,
    outputIndex: 0,
    pollInterval: null,
    shortcuts: [],
    currentPayload: null,
    pendingShortcut: null,
    listeners: [],
    sessions: []
};

// ─── Init ───────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadListeners();
    loadSessions();
    loadShortcuts();
    loadLocalIP();
    
    // Poll for updates every 2 seconds
    setInterval(() => {
        loadListeners();
        loadSessions();
        if (RS.activeSessionId) {
            pollSessionOutput();
        }
    }, 2000);
});

// ─── Listeners ──────────────────────────────────────────────────
async function loadListeners() {
    try {
        const res = await fetch('/revshell/api/listeners');
        const data = await res.json();
        RS.listeners = data.listeners || [];
        renderListeners();
    } catch (e) {
        console.error('Failed to load listeners:', e);
    }
}

function renderListeners() {
    const container = document.getElementById('listeners-list');
    const countEl = document.getElementById('listener-count');
    
    countEl.textContent = RS.listeners.length;
    
    if (RS.listeners.length === 0) {
        container.innerHTML = '<div style="color:#888;font-family:var(--font-mono);font-size:0.75rem;">No active listeners</div>';
        return;
    }
    
    container.innerHTML = RS.listeners.map(l => `
        <div class="listener-item">
            <div>
                <span class="port">:${l.port}</span>
                <span class="status ${l.running ? 'running' : 'stopped'}">${l.running ? '● LIVE' : '○ DEAD'}</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
                <span class="sessions-count">${l.session_count} session(s)</span>
                <button class="btn-sm danger" onclick="stopListener(${l.port})">STOP</button>
            </div>
        </div>
    `).join('');
}

async function startListener() {
    const portInput = document.getElementById('new-port');
    const port = parseInt(portInput.value);
    
    if (!port || port < 1 || port > 65535) {
        alert('Please enter a valid port (1-65535)');
        return;
    }
    
    try {
        const res = await fetch('/revshell/api/listeners/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ port })
        });
        const data = await res.json();
        if (data.success) {
            loadListeners();
            // Auto-fill payload port
            document.getElementById('payload-port').value = port;
        } else {
            alert('Error: ' + data.error);
        }
    } catch (e) {
        console.error('Failed to start listener:', e);
    }
}

async function stopListener(port) {
    if (!confirm(`Stop listener on port ${port}? This will close all associated sessions.`)) return;
    
    try {
        const res = await fetch('/revshell/api/listeners/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ port })
        });
        const data = await res.json();
        if (data.success) {
            // If active session was on this port, clear it
            if (RS.activeSessionId) {
                const session = RS.sessions.find(s => s.id === RS.activeSessionId);
                if (session && session.listener_port === port) {
                    clearActiveSession();
                }
            }
            loadListeners();
            loadSessions();
        }
    } catch (e) {
        console.error('Failed to stop listener:', e);
    }
}

// ─── Sessions ───────────────────────────────────────────────────
async function loadSessions() {
    try {
        const res = await fetch('/revshell/api/sessions');
        const data = await res.json();
        RS.sessions = data.sessions || [];
        renderSessionTabs();
    } catch (e) {
        console.error('Failed to load sessions:', e);
    }
}

function renderSessionTabs() {
    const container = document.getElementById('session-tabs');
    
    if (RS.sessions.length === 0) {
        container.innerHTML = '';
        return;
    }
    
    container.innerHTML = RS.sessions.map(s => {
        const osClass = s.os_type || 'unknown';
        const isActive = s.id === RS.activeSessionId;
        return `
            <button class="session-tab ${isActive ? 'active' : ''}" onclick="selectSession('${s.id}')">
                <span class="os-dot ${osClass}"></span>
                ${s.address} :${s.listener_port}
                ${!s.active ? ' [DEAD]' : ''}
            </button>
        `;
    }).join('');
}

function selectSession(sessionId) {
    RS.activeSessionId = sessionId;
    RS.outputIndex = 0;
    
    // Clear terminal
    const terminal = document.getElementById('terminal-output');
    terminal.innerHTML = '';
    
    // Enable input
    document.getElementById('terminal-input').disabled = false;
    document.getElementById('send-btn').disabled = false;
    
    // Update label
    const session = RS.sessions.find(s => s.id === sessionId);
    if (session) {
        document.getElementById('active-session-label').textContent = 
            `- ${session.address} (${session.os_type})`;
    }
    
    renderSessionTabs();
    pollSessionOutput();
}

function clearActiveSession() {
    RS.activeSessionId = null;
    RS.outputIndex = 0;
    document.getElementById('terminal-output').innerHTML = 
        '<div class="terminal-placeholder">[*] Waiting for connections...<br>Start a listener and select a session to begin.</div>';
    document.getElementById('terminal-input').disabled = true;
    document.getElementById('send-btn').disabled = true;
    document.getElementById('active-session-label').textContent = '- No session selected';
    renderSessionTabs();
}

async function closeActiveSession() {
    if (!RS.activeSessionId) return;
    if (!confirm('Close this session?')) return;
    
    try {
        const res = await fetch(`/revshell/api/sessions/${RS.activeSessionId}/close`, {
            method: 'POST'
        });
        const data = await res.json();
        if (data.success) {
            clearActiveSession();
            loadSessions();
        }
    } catch (e) {
        console.error('Failed to close session:', e);
    }
}

// ─── Terminal I/O ───────────────────────────────────────────────
async function pollSessionOutput() {
    if (!RS.activeSessionId) return;
    
    try {
        const res = await fetch(`/revshell/api/sessions/${RS.activeSessionId}/output?since=${RS.outputIndex}`);
        const data = await res.json();
        
        if (data.lines && data.lines.length > 0) {
            appendTerminalOutput(data.lines);
            RS.outputIndex = data.index;
        }
        
        // If session died, update UI
        if (!data.active) {
            const session = RS.sessions.find(s => s.id === RS.activeSessionId);
            if (session) session.active = false;
            renderSessionTabs();
            appendTerminalOutput(['\n[!] Session disconnected.']);
        }
    } catch (e) {
        console.error('Failed to poll output:', e);
    }
}

function appendTerminalOutput(lines) {
    const terminal = document.getElementById('terminal-output');
    // Remove placeholder if present
    const placeholder = terminal.querySelector('.terminal-placeholder');
    if (placeholder) placeholder.remove();
    
    for (const line of lines) {
        const div = document.createElement('div');
        div.textContent = line;
        terminal.appendChild(div);
    }
    
    // Auto-scroll to bottom
    terminal.scrollTop = terminal.scrollHeight;
}

async function sendCommand() {
    if (!RS.activeSessionId) return;
    
    const input = document.getElementById('terminal-input');
    const command = input.value.trim();
    if (!command) return;
    
    // Show command in terminal
    const terminal = document.getElementById('terminal-output');
    const placeholder = terminal.querySelector('.terminal-placeholder');
    if (placeholder) placeholder.remove();
    
    const cmdDiv = document.createElement('div');
    cmdDiv.className = 'cmd-line';
    cmdDiv.textContent = command;
    terminal.appendChild(cmdDiv);
    terminal.scrollTop = terminal.scrollHeight;
    
    input.value = '';
    
    try {
        const res = await fetch(`/revshell/api/sessions/${RS.activeSessionId}/send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command })
        });
        const data = await res.json();
        if (!data.success) {
            const errDiv = document.createElement('div');
            errDiv.className = 'error-line';
            errDiv.textContent = '[!] Failed to send command';
            terminal.appendChild(errDiv);
        }
    } catch (e) {
        console.error('Failed to send command:', e);
    }
}

function clearTerminal() {
    document.getElementById('terminal-output').innerHTML = '';
    RS.outputIndex = 0;
    if (!RS.activeSessionId) {
        document.getElementById('terminal-output').innerHTML = 
            '<div class="terminal-placeholder">[*] Waiting for connections...<br>Start a listener and select a session to begin.</div>';
    }
}

// ─── Payload Generator ──────────────────────────────────────────
async function generatePayloads() {
    const host = document.getElementById('payload-host').value.trim();
    const port = document.getElementById('payload-port').value.trim();
    
    if (!host || !port) {
        alert('Please enter LHOST and LPORT');
        return;
    }
    
    try {
        const res = await fetch(`/revshell/api/payloads?host=${encodeURIComponent(host)}&port=${port}`);
        const data = await res.json();
        
        if (data.payloads) {
            renderPayloads(data.payloads);
        }
    } catch (e) {
        console.error('Failed to generate payloads:', e);
    }
}

function renderPayloads(payloads) {
    const container = document.getElementById('payload-list');
    
    container.innerHTML = payloads.map(p => {
        if (p.error) {
            return `<div class="payload-item" style="color:#f44;">Error: ${p.error}</div>`;
        }
        return `
            <div class="payload-item" onclick="showPayload('${p.language}', '${p.platform}')" 
                 data-payload='${JSON.stringify(p).replace(/'/g, "&#39;")}'>
                <div class="pl-lang">${p.language.toUpperCase()}</div>
                <div class="pl-desc">${p.description}</div>
                <div style="font-size:0.65rem;color:var(--neon-cyan);margin-top:2px;">${p.platform}</div>
            </div>
        `;
    }).join('');
    
    // Store payloads for modal access
    RS._payloads = payloads;
}

function showPayload(language, platform) {
    const payload = RS._payloads.find(p => p.language === language && p.platform === platform);
    if (!payload) return;
    
    RS.currentPayload = payload;
    
    document.getElementById('payload-modal-title').textContent = 
        `PAYLOAD: ${payload.language.toUpperCase()} (${payload.platform})`;
    document.getElementById('payload-modal-code').textContent = payload.code;
    document.getElementById('payload-modal-usage').textContent = 
        `Usage: ${payload.usage || 'N/A'}`;
    document.getElementById('payload-modal').classList.add('show');
}

function closePayloadModal() {
    document.getElementById('payload-modal').classList.remove('show');
    RS.currentPayload = null;
}

function copyPayload() {
    if (!RS.currentPayload) return;
    navigator.clipboard.writeText(RS.currentPayload.code).then(() => {
        alert('Payload copied to clipboard!');
    }).catch(() => {
        // Fallback
        const ta = document.createElement('textarea');
        ta.value = RS.currentPayload.code;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        alert('Payload copied to clipboard!');
    });
}

function downloadPayload() {
    if (!RS.currentPayload) return;
    const blob = new Blob([RS.currentPayload.code], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = RS.currentPayload.name || 'payload.txt';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// ─── Shortcuts ──────────────────────────────────────────────────
async function loadShortcuts() {
    try {
        const res = await fetch('/revshell/api/shortcuts');
        const data = await res.json();
        RS.shortcuts = data.shortcuts || [];
        renderShortcuts();
    } catch (e) {
        console.error('Failed to load shortcuts:', e);
    }
}

function renderShortcuts() {
    const container = document.getElementById('shortcuts-list');
    
    if (RS.shortcuts.length === 0) {
        container.innerHTML = '<div style="color:#888;font-family:var(--font-mono);font-size:0.7rem;">No shortcuts loaded</div>';
        return;
    }
    
    // Group by category
    const categories = {};
    for (const sc of RS.shortcuts) {
        const cat = sc.category || 'general';
        if (!categories[cat]) categories[cat] = [];
        categories[cat].push(sc);
    }
    
    const iconMap = {
        'upload': '⬆', 'download': '⬇', 'shield': '🛡', 'plus-circle': '➕',
        'terminal': '💻', 'trash': '🗑', 'info': 'ℹ', 'key': '🔑'
    };
    
    let html = '';
    for (const [cat, shortcuts] of Object.entries(categories)) {
        html += `<div class="shortcut-category">${cat.toUpperCase()}</div>`;
        for (const sc of shortcuts) {
            html += `
                <button class="shortcut-btn" onclick="triggerShortcut('${sc.id}')" title="${sc.description}">
                    <span class="sc-icon">${iconMap[sc.icon] || '▶'}</span>
                    <span class="sc-name">${sc.name}</span>
                </button>
            `;
        }
    }
    
    container.innerHTML = html;
}

function triggerShortcut(shortcutId) {
    if (!RS.activeSessionId) {
        alert('Please select an active session first.');
        return;
    }
    
    const shortcut = RS.shortcuts.find(s => s.id === shortcutId);
    if (!shortcut) return;
    
    // If shortcut has params, show modal
    if (shortcut.params && shortcut.params.length > 0) {
        RS.pendingShortcut = shortcut;
        showShortcutModal(shortcut);
    } else {
        // Execute directly
        executeShortcut(shortcutId, {});
    }
}

function showShortcutModal(shortcut) {
    document.getElementById('shortcut-modal-title').textContent = 
        `SHORTCUT: ${shortcut.name}`;
    
    const paramsContainer = document.getElementById('shortcut-modal-params');
    paramsContainer.innerHTML = shortcut.params.map(p => {
        if (p.type === 'file') {
            return `
                <div class="sc-param-group">
                    <label>${p.label} ${p.required ? '*' : ''}</label>
                    <input type="file" id="sc-param-${p.name}" onchange="handleFileSelect('${p.name}')">
                </div>
            `;
        }
        if (p.type === 'textarea') {
            return `
                <div class="sc-param-group">
                    <label>${p.label} ${p.required ? '*' : ''}</label>
                    <textarea id="sc-param-${p.name}" placeholder="${p.label}"></textarea>
                </div>
            `;
        }
        return `
            <div class="sc-param-group">
                <label>${p.label} ${p.required ? '*' : ''}</label>
                <input type="${p.type || 'text'}" id="sc-param-${p.name}" placeholder="${p.label}">
            </div>
        `;
    }).join('');
    
    document.getElementById('shortcut-modal').classList.add('show');
}

function closeShortcutModal() {
    document.getElementById('shortcut-modal').classList.remove('show');
    RS.pendingShortcut = null;
    RS._fileData = null;
}

// Store file data for upload shortcut
RS._fileData = null;

function handleFileSelect(paramName) {
    const fileInput = document.getElementById(`sc-param-${paramName}`);
    if (!fileInput.files || !fileInput.files[0]) return;
    
    const file = fileInput.files[0];
    const reader = new FileReader();
    reader.onload = function(e) {
        // Extract base64 content
        const base64 = e.target.result.split(',')[1];
        RS._fileData = base64;
    };
    reader.readAsDataURL(file);
}

async function executeShortcutWithParams() {
    if (!RS.pendingShortcut) return;
    
    const params = {};
    for (const p of RS.pendingShortcut.params) {
        const el = document.getElementById(`sc-param-${p.name}`);
        if (el) {
            params[p.name] = el.value;
        }
    }
    
    // Add file data if present
    const payload = {
        shortcut_id: RS.pendingShortcut.id,
        session_id: RS.activeSessionId,
        params
    };
    
    if (RS._fileData) {
        payload.file_data = RS._fileData;
    }
    
    try {
        const res = await fetch('/revshell/api/shortcuts/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        
        closeShortcutModal();
        
        // Show result in terminal
        const terminal = document.getElementById('terminal-output');
        const placeholder = terminal.querySelector('.terminal-placeholder');
        if (placeholder) placeholder.remove();
        
        const resultDiv = document.createElement('div');
        resultDiv.style.color = data.success ? '#0f0' : '#f44';
        resultDiv.textContent = `[${data.success ? '+' : '!'}] ${data.message || data.error}`;
        terminal.appendChild(resultDiv);
        terminal.scrollTop = terminal.scrollHeight;
        
    } catch (e) {
        console.error('Failed to execute shortcut:', e);
        closeShortcutModal();
    }
}

async function executeShortcut(shortcutId, params) {
    try {
        const res = await fetch('/revshell/api/shortcuts/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                shortcut_id: shortcutId,
                session_id: RS.activeSessionId,
                params
            })
        });
        const data = await res.json();
        
        // Show result in terminal
        const terminal = document.getElementById('terminal-output');
        const placeholder = terminal.querySelector('.terminal-placeholder');
        if (placeholder) placeholder.remove();
        
        const resultDiv = document.createElement('div');
        resultDiv.style.color = data.success ? '#0f0' : '#f44';
        resultDiv.textContent = `[${data.success ? '+' : '!'}] ${data.message || data.error}`;
        terminal.appendChild(resultDiv);
        terminal.scrollTop = terminal.scrollHeight;
        
    } catch (e) {
        console.error('Failed to execute shortcut:', e);
    }
}

// ─── Utility ────────────────────────────────────────────────────
async function loadLocalIP() {
    try {
        const res = await fetch('/revshell/api/local_ip');
        const data = await res.json();
        document.getElementById('local-ip').textContent = data.ip;
        document.getElementById('payload-host').value = data.ip;
    } catch (e) {
        console.error('Failed to load local IP:', e);
    }
}
