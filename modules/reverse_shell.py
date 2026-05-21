"""
Reverse Shell Multi-Handler Module
Web-based dashboard for managing reverse shell listeners, sessions, payload generation, and shortcuts.
"""

import json
import base64
from flask import Blueprint, jsonify, request, render_template

from classes.reverse_shell.handler import ReverseShellManager
from classes.reverse_shell.payload_generator import PayloadGenerator
from classes.reverse_shell.shortcuts import ShortcutRegistry, register_default_shortcuts

# Register all default shortcuts at import time
register_default_shortcuts()

MODULE_NAME = "Reverse Shell Handler"
MODULE_DESCRIPTION = "Multi-handler reverse shell dashboard with payload generator, interactive webshell, and Meterpreter-style shortcuts."
MODULE_PREFIX = "/revshell"

module_bp = Blueprint('revshell', __name__, template_folder='../../templates', static_folder='../../static')

# ─── Page Routes ─────────────────────────────────────────────────

@module_bp.route('/')
def index():
    """Main dashboard page."""
    return render_template('reverse_shell/index.html')


# ─── Listener API ────────────────────────────────────────────────

@module_bp.route('/api/listeners', methods=['GET'])
def get_listeners():
    """Get all listeners status."""
    manager = ReverseShellManager()
    return jsonify({
        'listeners': manager.get_all_listeners(),
        'local_ip': manager.get_local_ip()
    })


@module_bp.route('/api/listeners/start', methods=['POST'])
def start_listener():
    """Start a new listener on a given port."""
    data = request.json
    port = data.get('port')
    
    if not port or not isinstance(port, int) or port < 1 or port > 65535:
        return jsonify({'success': False, 'error': 'Invalid port number (1-65535)'}), 400
    
    manager = ReverseShellManager()
    result = manager.start_listener(port)
    return jsonify(result)


@module_bp.route('/api/listeners/stop', methods=['POST'])
def stop_listener():
    """Stop a listener on a given port."""
    data = request.json
    port = data.get('port')
    
    if not port:
        return jsonify({'success': False, 'error': 'Port required'}), 400
    
    manager = ReverseShellManager()
    result = manager.stop_listener(port)
    return jsonify(result)


# ─── Session API ─────────────────────────────────────────────────

@module_bp.route('/api/sessions', methods=['GET'])
def get_sessions():
    """Get all active sessions."""
    manager = ReverseShellManager()
    return jsonify({'sessions': manager.get_all_sessions()})


@module_bp.route('/api/sessions/<session_id>', methods=['GET'])
def get_session(session_id):
    """Get session details."""
    manager = ReverseShellManager()
    session = manager.get_session(session_id)
    if not session:
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    return jsonify({'session': session.to_dict()})


@module_bp.route('/api/sessions/<session_id>/output', methods=['GET'])
def get_session_output(session_id):
    """Get session output since a given index."""
    manager = ReverseShellManager()
    session = manager.get_session(session_id)
    if not session:
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    
    since = request.args.get('since', 0, type=int)
    lines, new_index = session.get_output_since(since)
    return jsonify({
        'lines': lines,
        'index': new_index,
        'active': session.active
    })


@module_bp.route('/api/sessions/<session_id>/send', methods=['POST'])
def send_command(session_id):
    """Send a command to a session."""
    data = request.json
    command = data.get('command', '').strip()
    
    if not command:
        return jsonify({'success': False, 'error': 'No command provided'}), 400
    
    manager = ReverseShellManager()
    session = manager.get_session(session_id)
    if not session or not session.active:
        return jsonify({'success': False, 'error': 'Session not found or inactive'}), 404
    
    success = session.send_command(command)
    return jsonify({'success': success, 'command': command})


@module_bp.route('/api/sessions/<session_id>/close', methods=['POST'])
def close_session(session_id):
    """Close a session."""
    manager = ReverseShellManager()
    result = manager.close_session(session_id)
    return jsonify(result)


# ─── Payload Generator API ───────────────────────────────────────

@module_bp.route('/api/payloads', methods=['GET'])
def get_payloads():
    """Get all available payloads for a given host/port."""
    host = request.args.get('host', '')
    port = request.args.get('port', 0, type=int)
    
    if not host or not port:
        return jsonify({'success': False, 'error': 'Host and port required'}), 400
    
    payloads = PayloadGenerator.get_all_payloads(host, port)
    return jsonify({'payloads': payloads, 'host': host, 'port': port})


@module_bp.route('/api/payloads/<payload_type>', methods=['GET'])
def get_payload(payload_type):
    """Get a specific payload type."""
    host = request.args.get('host', '')
    port = request.args.get('port', 0, type=int)
    
    if not host or not port:
        return jsonify({'success': False, 'error': 'Host and port required'}), 400
    
    result = PayloadGenerator.get_payload_by_type(host, port, payload_type)
    return jsonify(result)


# ─── Shortcuts API ───────────────────────────────────────────────

@module_bp.route('/api/shortcuts', methods=['GET'])
def get_shortcuts():
    """Get all registered shortcuts."""
    shortcuts = ShortcutRegistry.get_all()
    return jsonify({'shortcuts': shortcuts})


@module_bp.route('/api/shortcuts/execute', methods=['POST'])
def execute_shortcut():
    """Execute a shortcut on a session."""
    data = request.json
    shortcut_id = data.get('shortcut_id')
    session_id = data.get('session_id')
    params = data.get('params', {})
    
    if not shortcut_id or not session_id:
        return jsonify({'success': False, 'error': 'shortcut_id and session_id required'}), 400
    
    # Handle file uploads (base64 content)
    if 'file_data' in data:
        params['content'] = data['file_data']
    
    result = ShortcutRegistry.execute(shortcut_id, session_id, params)
    return jsonify(result)


# ─── Utility API ─────────────────────────────────────────────────

@module_bp.route('/api/local_ip', methods=['GET'])
def get_local_ip():
    """Get the local IP address."""
    manager = ReverseShellManager()
    return jsonify({'ip': manager.get_local_ip()})


@module_bp.route('/api/shutdown_all', methods=['POST'])
def shutdown_all():
    """Stop all listeners and close all sessions."""
    manager = ReverseShellManager()
    manager.shutdown_all()
    return jsonify({'success': True, 'message': 'All listeners stopped, all sessions closed'})
