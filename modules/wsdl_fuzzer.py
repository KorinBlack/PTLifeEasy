import traceback
import requests
from flask import Blueprint, jsonify, request, render_template
import zeep
from utils import load_config
import time

MODULE_NAME = "WSDL Parser & Fuzzer"
MODULE_DESCRIPTION = "Parse WSDL files, manipulate SOAP actions, and run payload attacks on specific fields."
MODULE_PREFIX = "/wsdl"

module_bp = Blueprint('wsdl_fuzzer', __name__, template_folder='../../templates', static_folder='../../static')

@module_bp.route('/')
def index():
    return render_template('wsdl_fuzzer.html')

@module_bp.route('/api/parse', methods=['POST'])
def parse_wsdl():
    data = request.json
    wsdl_url = data.get('url')
    
    if not wsdl_url:
        return jsonify({"error": "No WSDL URL provided"}), 400
        
    try:
        settings = zeep.Settings(strict=False, xml_huge_tree=True)
        client = zeep.Client(wsdl_url, settings=settings)
        
        target_namespace = client.namespaces.get('tns', '')
        if not target_namespace:
            try:
                if hasattr(client.wsdl, 'target_namespace'):
                    target_namespace = client.wsdl.target_namespace
            except:
                pass

        actions = []
        for service in client.wsdl.services.values():
            for port in service.ports.values():
                for op_name, operation in port.binding._operations.items():
                    fields = []
                    try:
                        # Parse Zeep's human-readable signature to extract fields reliably
                        signature = operation.signature()
                        if '->' in signature:
                            input_part = signature.split(') ->')[0].split('(', 1)[1]
                            if input_part.strip():
                                # Handle nested commas if any, but usually it's "name: type, name2: type2"
                                params = input_part.split(', ')
                                for p in params:
                                    if ':' in p:
                                        name, t = p.split(':', 1)
                                        fields.append({
                                            "name": name.strip(), 
                                            "type": t.strip(), 
                                            "active": True, 
                                            "value": "?"
                                        })
                    except Exception as e:
                        print(f"Error parsing signature for {op_name}: {e}")
                        
                    actions.append({
                        "name": op_name,
                        "service": service.name,
                        "port": port.name,
                        "active": True,
                        "fields": fields,
                        "target_namespace": target_namespace,
                        "soap_action": operation.soapaction or ""
                    })
                    
        return jsonify({"actions": actions, "target_namespace": target_namespace})
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500

def execute_soap_request(wsdl_url, action_data, field_values, target_namespace):
    # Construct a raw SOAP envelope
    # This allows us to bypass Zeep's strict type checking for fuzzing
    op_name = action_data['name']
    soap_action = action_data.get('soap_action', '')
    
    # Simple XML Builder
    xml_fields = ""
    for field in action_data.get('fields', []):
        if field['active']:
            val = field_values.get(field['name'], field['value'])
            xml_fields += f"<{field['name']}>{val}</{field['name']}>\n"
            
    envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <{op_name} xmlns="{target_namespace}">
{xml_fields}
    </{op_name}>
  </soap:Body>
</soap:Envelope>"""

    # Global proxy config
    config = load_config()
    proxies = {}
    if config.get('proxy_enabled'):
        if config.get('proxy_http'): proxies['http'] = config['proxy_http']
        if config.get('proxy_https'): proxies['https'] = config['proxy_https']
        
    headers = {
        'Content-Type': 'text/xml; charset=utf-8',
        'SOAPAction': soap_action if soap_action else f"{target_namespace}/{op_name}"
    }
    
    # Add custom headers
    if config.get('custom_headers'):
        headers.update(config['custom_headers'])
        
    start_time = time.time()
    try:
        # Determine endpoint URL (often the same as WSDL but without ?wsdl)
        endpoint = wsdl_url.split('?')[0] if '?' in wsdl_url else wsdl_url
        
        # We should use the actual port binding URL if Zeep provided it, but typically endpoint is fine for simple WSDLs
        res = requests.post(endpoint, data=envelope.encode('utf-8'), headers=headers, proxies=proxies, verify=False, timeout=10)
        elapsed = int((time.time() - start_time) * 1000)
        
        return {
            "status": res.status_code,
            "length": len(res.text),
            "time_ms": elapsed,
            "success": True,
            "error": None
        }
    except Exception as e:
        elapsed = int((time.time() - start_time) * 1000)
        return {
            "status": 0,
            "length": 0,
            "time_ms": elapsed,
            "success": False,
            "error": str(e)
        }

@module_bp.route('/api/attack_field', methods=['POST'])
def attack_field():
    data = request.json
    wsdl_url = data.get('url')
    action = data.get('action')
    target_field_name = data.get('target_field')
    payloads = data.get('payloads', [])
    target_namespace = data.get('target_namespace', '')
    
    results = []
    base_values = {f['name']: f['value'] for f in action['fields']}
    
    for payload in payloads:
        if not payload.strip():
            continue
            
        # Clone base values and inject payload
        current_values = base_values.copy()
        current_values[target_field_name] = payload
        
        res = execute_soap_request(wsdl_url, action, current_values, target_namespace)
        res['payload'] = payload
        results.append(res)
        
    return jsonify({"results": results})

@module_bp.route('/api/run_collection', methods=['POST'])
def run_collection():
    data = request.json
    wsdl_url = data.get('url')
    actions = data.get('actions', [])
    payloads = data.get('payloads', [])
    target_namespace = data.get('target_namespace', '')
    
    all_results = []
    
    for action in actions:
        if not action.get('active'):
            continue
            
        base_values = {f['name']: f['value'] for f in action['fields']}
        
        for field in action.get('fields', []):
            if not field.get('active'):
                continue
                
            for payload in payloads:
                if not payload.strip():
                    continue
                    
                current_values = base_values.copy()
                current_values[field['name']] = payload
                
                res = execute_soap_request(wsdl_url, action, current_values, target_namespace)
                res['action_name'] = action['name']
                res['field_name'] = field['name']
                res['payload'] = payload
                all_results.append(res)
                
    return jsonify({"results": all_results})
