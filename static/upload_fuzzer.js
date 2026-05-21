/**
 * PTLifeEasy - Upload Fuzzer Dashboard
 * Handles configuration, fuzzing execution, progress tracking, and results matrix display.
 */

const UF = {
    jobId: null,
    pollInterval: null,
    results: [],
    summary: null,
    baselineFile: null,       // File object
    baselineB64: null,        // Base64-encoded content
    baselineFilename: null,
    baselineContentType: null
};

var stop = false;

// ─── Init ───────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('uf-start-btn').addEventListener('click', startFuzz);
    document.getElementById('uf-stop-btn').addEventListener('click', stopFuzz);
    setupBaselineDropzone();
});

// ─── Baseline File Handling ────────────────────────────────────
function setupBaselineDropzone() {
    const dropzone = document.getElementById('uf-baseline-dropzone');
    const fileInput = document.getElementById('uf-baseline-input');

    // Click to browse
    dropzone.addEventListener('click', () => fileInput.click());

    // Drag & drop
    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('dragover');
    });

    dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove('dragover');
    });

    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            processBaselineFile(e.dataTransfer.files[0]);
        }
    });
}

function handleBaselineFile(input) {
    if (input.files.length > 0) {
        processBaselineFile(input.files[0]);
    }
}

function processBaselineFile(file) {
    UF.baselineFile = file;
    UF.baselineFilename = file.name;
    UF.baselineContentType = file.type || 'application/octet-stream';

    const reader = new FileReader();
    reader.onload = function(e) {
        // Extract base64 content (strip data:...;base64, prefix)
        const result = e.target.result;
        const b64 = result.split(',')[1];
        UF.baselineB64 = b64;

        // Show file info
        document.getElementById('uf-baseline-dropzone').style.display = 'none';
        document.getElementById('uf-baseline-info').style.display = 'flex';
        document.getElementById('uf-baseline-filename').textContent = file.name;
        document.getElementById('uf-baseline-size').textContent = formatBytes(file.size);
    };
    reader.readAsDataURL(file);
}

function removeBaseline() {
    UF.baselineFile = null;
    UF.baselineB64 = null;
    UF.baselineFilename = null;
    UF.baselineContentType = null;

    document.getElementById('uf-baseline-dropzone').style.display = 'flex';
    document.getElementById('uf-baseline-info').style.display = 'none';
    document.getElementById('uf-baseline-input').value = '';
}

// ─── Start / Stop ──────────────────────────────────────────────
async function startFuzz() {
    stop = false;
    const url = document.getElementById('uf-url').value.trim();
    if (!url) {
        alert('Please enter a target URL');
        return;
    }

    const payload = {
        url: url,
        field_name: document.getElementById('uf-field-name').value.trim() || 'file',
        timeout: parseInt(document.getElementById('uf-timeout').value) || 15,
        cookies: document.getElementById('uf-cookies').value.trim(),
        headers: document.getElementById('uf-headers').value.trim(),
        extra_fields: document.getElementById('uf-extra-fields').value.trim(),
        enable_waf: document.getElementById('uf-waf-mode').checked,
        payload_mode: document.getElementById('uf-payload-mode').checked ? 'real' : 'benign',
        success_keywords: document.getElementById('uf-keywords').value.trim(),
        success_regex: document.getElementById('uf-regex').value.trim(),
        enabled_extensions: document.getElementById('uf-enabled-extensions').value.trim()
    };

    // Attach custom baseline if provided
    if (UF.baselineB64) {
        payload.baseline_file_b64 = UF.baselineB64;
        payload.baseline_filename = UF.baselineFilename;
        payload.baseline_content_type = UF.baselineContentType;
    }

    // UI state
    setRunning(true);
    document.getElementById('uf-status-badge').textContent = 'RUNNING';
    document.getElementById('uf-status-badge').className = 'uf-badge running';

    try {
        const res = await fetch('/upload_fuzzer/api/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.success) {
            UF.jobId = data.job_id;
            document.getElementById('uf-progress-container').style.display = 'block';
            document.getElementById('uf-progress-label').textContent = `0 / ${data.total_techniques}`;
            UF.pollInterval = setInterval(pollProgress, 500);
        } else {
            alert('Error: ' + data.error);
            setRunning(false);
        }
    } catch (e) {
        console.error('Failed to start fuzzing:', e);
        alert('Failed to start fuzzing: ' + e.message);
        setRunning(false);
    }
}

async function stopFuzz() {
    if (!UF.jobId) return;
    try {
        await fetch(`/upload_fuzzer/api/stop/${UF.jobId}`, { method: 'POST' });
    } catch (e) {
        console.error('Failed to stop:', e);
    }
    setRunning(false);
    stop = true;
}

function setRunning(running) {
    document.getElementById('uf-start-btn').style.display = running ? 'none' : 'block';
    document.getElementById('uf-stop-btn').style.display = running ? 'block' : 'none';

    if (!running) {
        if (UF.pollInterval) {
            clearInterval(UF.pollInterval);
            UF.pollInterval = null;
        }
        stop = true;
    }
}

// ─── Progress Polling ──────────────────────────────────────────
async function pollProgress() {
    if (!UF.jobId) return;

    if (stop) {
        return;
    }

    try {
        const res = await fetch(`/upload_fuzzer/api/progress/${UF.jobId}`);
        const data = await res.json();

        if (!data.success) return;

        // Update progress bar
        const pct = data.total > 0 ? Math.round((data.progress / data.total) * 100) : 0;
        document.getElementById('uf-progress-fill').style.width = pct + '%';
        document.getElementById('uf-progress-label').textContent = `${data.progress} / ${data.total}`;
        document.getElementById('uf-progress-current').textContent = data.current || '';

        // Check if completed
        if (data.status === 'completed' || data.status === 'stopped' || data.status === 'error') {
            clearInterval(UF.pollInterval);
            UF.pollInterval = null;
            setRunning(false);
            document.getElementById('uf-progress-container').style.display = 'none';

            if (data.status === 'completed') {
                UF.results = data.results || [];
                UF.summary = data.summary;
                renderSummary(data.summary);
                renderResults(data.results);
                document.getElementById('uf-status-badge').textContent = 'DONE';
                document.getElementById('uf-status-badge').className = 'uf-badge done';
            } else if (data.status === 'stopped') {
                document.getElementById('uf-status-badge').textContent = 'STOPPED';
                document.getElementById('uf-status-badge').className = 'uf-badge stopped';
            } else {
                document.getElementById('uf-status-badge').textContent = 'ERROR';
                document.getElementById('uf-status-badge').className = 'uf-badge error';
                alert('Error: ' + (data.error || 'Unknown error'));
            }
        }
    } catch (e) {
        console.error('Poll error:', e);
    }
}

// ─── Results Rendering ─────────────────────────────────────────
function renderSummary(summary) {
    if (!summary) return;

    const container = document.getElementById('uf-summary');
    container.style.display = 'block';

    const statusDist = Object.entries(summary.status_code_distribution || {})
        .map(([code, count]) => `<span class="uf-stat-chip">${code}: ${count}</span>`)
        .join(' ');

    container.innerHTML = `
        <div class="uf-summary-grid">
            <div class="uf-summary-item">
                <span class="uf-summary-label">Techniques</span>
                <span class="uf-summary-value">${summary.total_techniques}</span>
            </div>
            <div class="uf-summary-item">
                <span class="uf-summary-label">Baseline Status</span>
                <span class="uf-summary-value">${summary.baseline_status || 'N/A'}</span>
            </div>
            <div class="uf-summary-item">
                <span class="uf-summary-label">Keyword Matches</span>
                <span class="uf-summary-value ${summary.keyword_matches > 0 ? 'highlight' : ''}">${summary.keyword_matches}</span>
            </div>
            <div class="uf-summary-item">
                <span class="uf-summary-label">Regex Matches</span>
                <span class="uf-summary-value ${summary.regex_matches > 0 ? 'highlight' : ''}">${summary.regex_matches}</span>
            </div>
            <div class="uf-summary-item">
                <span class="uf-summary-label">Potential Bypasses</span>
                <span class="uf-summary-value ${summary.potential_bypass_count > 0 ? 'danger' : ''}">${summary.potential_bypass_count}</span>
            </div>
            <div class="uf-summary-item">
                <span class="uf-summary-label">WAF Mode</span>
                <span class="uf-summary-value">${summary.waf_mode ? 'ON' : 'OFF'}</span>
            </div>
        </div>
        <div class="uf-summary-statuses">${statusDist}</div>
    `;
}

function renderResults(results) {
    if (!results || results.length === 0) return;

    const tbody = document.getElementById('uf-results-body');

    tbody.innerHTML = results.map((r, i) => {
        // Determine match status
        let matchHtml = '-';
        let matchClass = '';

        if (r.error) {
            matchHtml = 'ERROR';
            matchClass = 'cell-error';
        } else if (r.keyword_match === true || r.regex_match === true) {
            matchHtml = '✓ MATCH';
            matchClass = 'cell-match';
        } else if (r.keyword_match === false || r.regex_match === false) {
            matchHtml = '✗ NO';
            matchClass = 'cell-nomatch';
        }

        // Status code class
        let statusClass = '';
        const code = r.status_code;
        if (code >= 200 && code < 300) statusClass = 'cell-success';
        else if (code === 0) statusClass = 'cell-error';
        else if (code >= 400 && code < 500) statusClass = 'cell-client-err';
        else if (code >= 500) statusClass = 'cell-server-err';

        // Category badge
        const catColors = {
            'baseline': '#888',
            'extension': '#00bcd4',
            'alt_ext': '#9c27b0',
            'mime': '#ff9800',
            'magic': '#4caf50',
            'header': '#2196f3',
            'server': '#e91e63',
            'path': '#ff5722',
            'waf': '#ffeb3b'
        };
        const catColor = catColors[r.category] || '#888';

        // Matched keywords
        const kwDisplay = r.matched_keywords && r.matched_keywords.length > 0
            ? r.matched_keywords.join(', ')
            : '';

        return `
            <tr class="uf-result-row ${matchClass}" onclick="showDetail(${i})">
                <td>${i}</td>
                <td title="${r.technique_name}">${r.technique_name}</td>
                <td><span class="uf-cat-badge" style="background:${catColor}">${r.category}</span></td>
                <td><code>${r.extension}</code></td>
                <td><code>${r.content_type}</code></td>
                <td class="${statusClass}">${r.status_code || 'ERR'}</td>
                <td>${formatBytes(r.response_size)}</td>
                <td>${r.response_time_ms}ms</td>
                <td class="${matchClass}">${matchHtml}</td>
                <td class="uf-detail-cell">
                    ${kwDisplay ? `<span class="uf-kw-chip">${kwDisplay}</span>` : ''}
                    ${r.error ? `<span class="uf-err-chip">${r.error}</span>` : ''}
                </td>
            </tr>
        `;
    }).join('');
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// ─── Detail Modal ──────────────────────────────────────────────
function showDetail(index) {
    const result = UF.results[index];
    if (!result) return;

    document.getElementById('uf-detail-title').textContent =
        `REQUEST: ${result.technique_name} (${result.category})`;

    // Format headers
    const headers = result.request_headers || {};
    const headerStr = Object.entries(headers)
        .map(([k, v]) => `${k}: ${v}`)
        .join('\n');

    document.getElementById('uf-detail-headers').textContent = headerStr || 'N/A';
    document.getElementById('uf-detail-body').textContent = result.request_body_preview || 'N/A';

    document.getElementById('uf-detail-modal').classList.add('show');
}

function closeDetailModal() {
    document.getElementById('uf-detail-modal').classList.remove('show');
}

// Close modal on overlay click
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('uf-modal-overlay')) {
        e.target.classList.remove('show');
    }
});
