"""
Upload Fuzzer Module
Automated security testing for file upload functionality with bypass techniques and response analysis.
"""

import json
import base64
import threading
from flask import Blueprint, jsonify, request, render_template

from classes.upload_fuzzer.core import UploadFuzzerCore
from utils import load_config

MODULE_NAME = "Upload Fuzzer"
MODULE_DESCRIPTION = "Automated upload security testing with extension bypass, MIME spoofing, magic bytes, WAF bypass, and response analysis matrix."
MODULE_PREFIX = "/upload_fuzzer"

module_bp = Blueprint('upload_fuzzer', __name__, template_folder='../../templates', static_folder='../../static')

# Store running fuzz jobs for progress tracking
_running_jobs = {}
_job_lock = threading.Lock()


@module_bp.route('/')
def index():
    return render_template('upload_fuzzer/index.html')


# ─── Fuzzing API ────────────────────────────────────────────────

@module_bp.route('/api/start', methods=['POST'])
def start_fuzz():
    """Start a new fuzzing session."""
    data = request.json

    url = data.get('url', '').strip()
    if not url:
        return jsonify({'success': False, 'error': 'Target URL is required'}), 400

    field_name = data.get('field_name', 'file')
    cookies = data.get('cookies', '')
    custom_headers = data.get('headers', '')
    timeout = data.get('timeout', 15)
    payload_mode = data.get('payload_mode', 'benign')
    enable_waf = data.get('enable_waf', False)
    success_keywords = data.get('success_keywords', '')
    success_regex = data.get('success_regex', '')
    extra_fields_raw = data.get('extra_fields', '')
    enabled_extensions_raw = data.get('enabled_extensions', '')

    # Parse extra fields
    extra_fields = {}
    if extra_fields_raw:
        for line in extra_fields_raw.strip().split('\n'):
            if '=' in line:
                k, v = line.split('=', 1)
                extra_fields[k.strip()] = v.strip()

    # Parse enabled extensions (comma-separated, e.g. "jpg,png,pdf")
    enabled_extensions = []
    if enabled_extensions_raw:
        enabled_extensions = [e.strip().lstrip('.') for e in enabled_extensions_raw.split(',') if e.strip()]

    # Parse custom baseline file (base64-encoded)
    custom_baseline = None
    custom_baseline_filename = None
    custom_baseline_content_type = None
    baseline_b64 = data.get('baseline_file_b64', '')
    if baseline_b64:
        try:
            custom_baseline = base64.b64decode(baseline_b64)
            custom_baseline_filename = data.get('baseline_filename', 'custom_baseline')
            custom_baseline_content_type = data.get('baseline_content_type', 'application/octet-stream')
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid baseline file base64 data'}), 400

    # Build proxy from global config
    config = load_config()
    proxy = None
    if config.get('proxy_enabled'):
        proxy = {
            'http': config.get('proxy_http', ''),
            'https': config.get('proxy_https', '')
        }

    # Create fuzzer instance
    fuzzer = UploadFuzzerCore(
        url=url,
        field_name=field_name,
        cookies=cookies,
        headers=custom_headers,
        proxy=proxy,
        timeout=timeout,
        payload_mode=payload_mode,
        enable_waf=enable_waf,
        success_keywords=success_keywords,
        success_regex=success_regex,
        extra_fields=extra_fields,
        custom_baseline=custom_baseline,
        custom_baseline_filename=custom_baseline_filename,
        custom_baseline_content_type=custom_baseline_content_type,
        enabled_extensions=enabled_extensions
    )

    # Generate job ID
    import uuid
    job_id = str(uuid.uuid4())[:8]

    # Store job
    with _job_lock:
        _running_jobs[job_id] = {
            'fuzzer': fuzzer,
            'status': 'running',
            'progress': 0,
            'total': 0,
            'current': '',
            'results': None,
            'summary': None
        }

    # Run in background thread
    def run_fuzz():
        try:
            results = fuzzer.run_all(
                progress_callback=lambda current, total, name: _update_progress(job_id, current, total, name)
            )
            summary = fuzzer.get_summary()
            with _job_lock:
                if job_id in _running_jobs:
                    _running_jobs[job_id]['status'] = 'completed'
                    _running_jobs[job_id]['results'] = results
                    _running_jobs[job_id]['summary'] = summary
        except Exception as e:
            with _job_lock:
                if job_id in _running_jobs:
                    _running_jobs[job_id]['status'] = 'error'
                    _running_jobs[job_id]['error'] = str(e)

    thread = threading.Thread(target=run_fuzz, daemon=True)
    thread.start()

    return jsonify({
        'success': True,
        'job_id': job_id,
        'message': 'Fuzzing started',
        'total_techniques': len(fuzzer.STANDARD_TECHNIQUES) + (len(fuzzer.WAF_TECHNIQUES) if enable_waf else 0)
    })


def _update_progress(job_id: str, current: int, total: int, name: str):
    """Update progress for a running job."""
    with _job_lock:
        if job_id in _running_jobs:
            _running_jobs[job_id]['progress'] = current
            _running_jobs[job_id]['total'] = total
            _running_jobs[job_id]['current'] = name


@module_bp.route('/api/progress/<job_id>', methods=['GET'])
def get_progress(job_id):
    """Get progress of a running fuzz job."""
    with _job_lock:
        job = _running_jobs.get(job_id)
        if not job:
            return jsonify({'success': False, 'error': 'Job not found'}), 404

        return jsonify({
            'success': True,
            'job_id': job_id,
            'status': job['status'],
            'progress': job['progress'],
            'total': job['total'],
            'current': job['current'],
            'results': job['results'],
            'summary': job['summary'],
            'error': job.get('error')
        })


@module_bp.route('/api/stop/<job_id>', methods=['POST'])
def stop_fuzz(job_id):
    """Stop a running fuzz job."""
    with _job_lock:
        if job_id in _running_jobs:
            _running_jobs[job_id]['status'] = 'stopped'
            return jsonify({'success': True, 'message': 'Job stopped'})
        return jsonify({'success': False, 'error': 'Job not found'}), 404


@module_bp.route('/api/jobs', methods=['GET'])
def list_jobs():
    """List all jobs."""
    with _job_lock:
        jobs = []
        for jid, job in _running_jobs.items():
            jobs.append({
                'job_id': jid,
                'status': job['status'],
                'progress': job['progress'],
                'total': job['total']
            })
        return jsonify({'jobs': jobs})


# ─── Technique Info API ─────────────────────────────────────────

@module_bp.route('/api/techniques', methods=['GET'])
def get_techniques():
    """Get all available techniques with descriptions."""
    standard = []
    for t in UploadFuzzerCore.STANDARD_TECHNIQUES:
        standard.append({
            'id': t['id'],
            'name': t['name'],
            'category': t['category'],
            'extension': t['ext'],
            'content_type': t['ct'],
            'description': t['desc']
        })

    waf = []
    for t in UploadFuzzerCore.WAF_TECHNIQUES:
        waf.append({
            'id': t['id'],
            'name': t['name'],
            'category': t['category'],
            'extension': t['ext'],
            'content_type': t['ct'],
            'description': t['desc']
        })

    return jsonify({
        'standard': standard,
        'waf': waf,
        'standard_count': len(standard),
        'waf_count': len(waf),
        'total': len(standard) + len(waf)
    })
