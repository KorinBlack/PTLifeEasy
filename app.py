import os
import sys
import importlib
from flask import Flask, render_template, request, redirect, url_for, flash
from utils import load_config, save_config

app = Flask(__name__)
app.secret_key = 'cyberpunk_secret_key_change_in_production_choom'

# List to keep track of loaded modules for the frontend
loaded_modules = []

def load_modules():
    modules_dir = os.path.join(os.path.dirname(__file__), 'modules')
    if not os.path.exists(modules_dir):
        os.makedirs(modules_dir)
        with open(os.path.join(modules_dir, '__init__.py'), 'w') as f:
            f.write('')
            
    for item in os.listdir(modules_dir):
        item_path = os.path.join(modules_dir, item)
        
        module_name = None
        if os.path.isdir(item_path) and os.path.exists(os.path.join(item_path, '__init__.py')):
            module_name = item
        elif item.endswith('.py') and item != '__init__.py':
            module_name = item[:-3]
            
        if module_name:
            try:
                mod = importlib.import_module(f'modules.{module_name}')
                # Look for a Blueprint named 'module_bp'
                if hasattr(mod, 'module_bp'):
                    # Prefix endpoints with the module name or a custom one if provided
                    url_prefix = getattr(mod, 'MODULE_PREFIX', f'/{module_name}')
                    app.register_blueprint(mod.module_bp, url_prefix=url_prefix)
                    
                    module_info = {
                        'name': getattr(mod, 'MODULE_NAME', module_name),
                        'description': getattr(mod, 'MODULE_DESCRIPTION', 'No description provided.'),
                        'url': url_prefix
                    }
                    loaded_modules.append(module_info)
                    print(f"[*] Loaded module: {module_name} at {url_prefix}")
            except Exception as e:
                print(f"[!] Error loading module {module_name}: {e}")

# Load modules at startup
load_modules()

@app.route('/')
def index():
    return render_template('index.html', modules=loaded_modules)

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    config = load_config()
    if request.method == 'POST':
        try:
            config['port'] = int(request.form.get('port', 5000))
            config['proxy_enabled'] = 'proxy_enabled' in request.form
            config['proxy_http'] = request.form.get('proxy_http', '')
            config['proxy_https'] = request.form.get('proxy_https', '')
            
            headers = {}
            header_keys = request.form.getlist('header_key[]')
            header_values = request.form.getlist('header_value[]')
            for k, v in zip(header_keys, header_values):
                if k.strip():
                    headers[k.strip()] = v.strip()
            config['custom_headers'] = headers
            
            save_config(config)
            flash('Configuration saved! The server will restart automatically to apply changes.', 'success')
            return redirect(url_for('settings'))
        except Exception as e:
            flash(f'Error saving config: {e}', 'error')
            
    return render_template('settings.html', config=config)

if __name__ == '__main__':
    config = load_config()
    port = config.get('port', 5000)
    
    extra_files = [os.path.join(os.path.dirname(__file__), 'config.json')]
    
    # Using app.run with extra_files triggers a restart when config.json is modified
    print(f"[*] INIT: Starting PTLifeEasy on port {port}")
    app.run(host='0.0.0.0', port=port, debug=True, extra_files=extra_files)
