import traceback
from flask import Blueprint, jsonify, request, render_template
from classes.wsdl_fuzzer.wsdl_fuzzer_core import WSDLFuzzerCore

MODULE_NAME = "WSDL Parser & Fuzzer"
MODULE_DESCRIPTION = "Parse WSDL files, manipulate SOAP actions, and run payload attacks on specific fields."
MODULE_PREFIX = "/wsdl"

module_bp = Blueprint('wsdl_fuzzer', __name__, template_folder='../../templates', static_folder='../../static')

@module_bp.route('/')
def index():
    return render_template('wsdl_fuzzer/index.html')

@module_bp.route('/api/parse', methods=['POST'])
def parse_wsdl():
    data = request.json
    wsdl_url = data.get('url')
    if not wsdl_url:
        return jsonify({"error": "No WSDL URL provided"}), 400
        
    try:
        fuzzer = WSDLFuzzerCore(wsdl_url, data.get('headers', ''))
        result = fuzzer.parse()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500

@module_bp.route('/api/attack_field', methods=['POST'])
def attack_field():
    data = request.json
    fuzzer = WSDLFuzzerCore(data.get('url'), data.get('headers', ''))
    results = fuzzer.attack_field(
        action=data.get('action'),
        target_field_name=data.get('target_field'),
        payloads=data.get('payloads', []),
        target_namespace=data.get('target_namespace', '')
    )
    return jsonify({"results": results})

@module_bp.route('/api/play_request', methods=['POST'])
def play_request():
    data = request.json
    fuzzer = WSDLFuzzerCore(data.get('url'), data.get('headers', ''))
    results = fuzzer.execute_request(
        action_data=data.get('action'),
        field_values=data.get('field_values', {}),
        target_namespace=data.get('target_namespace', '')
    )
    return jsonify({"results": results})

@module_bp.route('/api/run_collection', methods=['POST'])
def run_collection():
    data = request.json
    fuzzer = WSDLFuzzerCore(data.get('url'), data.get('headers', ''))
    results = fuzzer.run_collection(
        actions=data.get('actions', []),
        payloads=data.get('payloads', []),
        target_namespace=data.get('target_namespace', '')
    )
    return jsonify({"results": results})

@module_bp.route('/api/attack_xxe', methods=['POST'])
def attack_xxe():
    data = request.json
    fuzzer = WSDLFuzzerCore(data.get('url'), data.get('headers', ''))
    results = fuzzer.attack_xxe(
        action=data.get('action'),
        payloads=data.get('payloads', []),
        target_namespace=data.get('target_namespace', '')
    )
    return jsonify({"results": results})
