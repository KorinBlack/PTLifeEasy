# PTLifeEasy Module Creation Guide

Welcome to the PTLifeEasy framework. This system is designed to be highly modular, allowing you to quickly integrate new tools and dashboards for authorized penetration testing activities.

## How the Module System Works

PTLifeEasy automatically scans the `modules/` directory at startup. It looks for Python files (or directories containing an `__init__.py`) and attempts to load them.

To be successfully loaded, your module **must** expose a Flask `Blueprint` object named `module_bp`.

## Creating a Module: Step-by-Step

1. **Create a new Python file** inside the `modules/` directory (e.g., `modules/my_tool.py`).
2. **Import `Blueprint`** from Flask.
3. **Define the metadata** variables (optional but recommended for the UI):
   - `MODULE_NAME`: The display name of your module.
   - `MODULE_DESCRIPTION`: A short description of what it does.
   - `MODULE_PREFIX`: The URL prefix for this module's routes (defaults to `/<filename>`).
4. **Create the `module_bp`** object.
5. **Define your routes** using `@module_bp.route(...)`.
6. **Access Global Configs** (Optional): If your module needs to know the global proxy settings or custom headers, you can import `load_config` from `utils`.

## Boilerplate Example

Here is a complete example of a simple module. You can save this as `modules/nmap_parser.py` to see it in action.

```python
from flask import Blueprint, jsonify, render_template_string
from utils import load_config

# 1. Metadata for the UI Dashboard
MODULE_NAME = "Nmap Output Parser"
MODULE_DESCRIPTION = "Upload and parse Nmap XML outputs to extract open ports and services."
MODULE_PREFIX = "/nmap"  # All routes will start with /nmap

# 2. Create the Blueprint MUST be named 'module_bp'
module_bp = Blueprint('nmap_parser', __name__)

# 3. Define Routes
@module_bp.route('/')
def index():
    # You can return templates, JSON, or simple strings
    # For complex UIs, place your templates in the main 'templates' folder 
    # and use render_template('your_module_template.html')
    
    html_content = """
    <div style="color: var(--neon-cyan); font-family: var(--font-mono);">
        <h2>Nmap Parser Subsystem</h2>
        <p>System Ready. Waiting for XML input...</p>
        <button class="cyber-btn-primary" onclick="alert('Not implemented yet!')">UPLOAD_XML</button>
        <br><br>
        <a href="/" class="cyber-btn" style="font-size: 1rem;"><< RETURN_TO_CORE</a>
    </div>
    """
    
    # We can wrap it in the base template by extending it if we had a dedicated file,
    # but for this example, we'll just return raw HTML styled with our global CSS.
    # To properly integrate with the Cyberpunk UI, create a template extending base.html.
    
    return html_content

@module_bp.route('/api/status')
def status():
    # Access global configs
    config = load_config()
    proxy_status = "ENABLED" if config.get('proxy_enabled') else "DISABLED"
    
    return jsonify({
        "module": "Nmap Parser",
        "status": "online",
        "global_proxy": proxy_status
    })
```

## Best Practices

- **Templates**: If your module needs its own HTML pages, create them in the main `templates/` directory (e.g., `templates/nmap_index.html`) and use `{% extends 'base.html' %}` to inherit the Cyberpunk aesthetic and navigation bar.
- **Static Files**: Any specific JS/CSS for your module should ideally be placed in the main `static/` directory, or you can configure your Blueprint to serve its own static folder.
- **Dependencies**: If your module introduces new pip requirements (like `nmap`, `requests`, `bs4`), make sure to add them to `requirements.txt`.
- **Class-Based Architecture (For Large Modules)**: For complex modules with heavy business logic, avoid putting all your code directly inside the Flask routes. Instead, encapsulate your core operations within dedicated Python classes (placed in the `classes/` directory). Your module file (`modules/my_tool.py`) should primarily handle request routing and response formatting, delegating the actual processing to your core classes. This decouples your logic from the web framework, significantly improving maintainability, testability, and potential CLI reuse.
