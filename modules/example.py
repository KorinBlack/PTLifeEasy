from flask import Blueprint, jsonify, render_template_string
from utils import load_config

MODULE_NAME = "Example Network Scanner"
MODULE_DESCRIPTION = "A dummy module demonstrating how to create endpoints for PTLifeEasy."
MODULE_PREFIX = "/scanner"

module_bp = Blueprint('scanner', __name__)

@module_bp.route('/')
def index():
    config = load_config()
    proxy_info = f"Proxy is {'ENABLED' if config.get('proxy_enabled') else 'DISABLED'}."
    
    content = """
    {% extends 'base.html' %}
    {% block title %}Scanner Module{% endblock %}
    {% block content %}
        <h1 class="page-title">SCANNER_MODULE</h1>
        <div class="module-card" style="margin-bottom: 20px;">
            <p style="color: var(--neon-cyan); font-family: var(--font-mono); font-size: 1.2rem;">
                This is a dynamically loaded module.
                <br>
                Global System info: {{ proxy_info }}
            </p>
        </div>
        <a href="/" class="cyber-btn"><< BACK_TO_CORE</a>
    {% endblock %}
    """
    return render_template_string(content, proxy_info=proxy_info)

@module_bp.route('/api/ping')
def api_ping():
    return jsonify({"status": "PONG", "message": "Scanner module is alive."})
