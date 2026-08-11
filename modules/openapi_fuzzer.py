import traceback
from flask import Blueprint, jsonify, request, render_template
from classes.openapi_fuzzer.openapi_fuzzer_core import OpenAPIFuzzerCore

MODULE_NAME = "OpenAPI/Swagger Parser & Fuzzer"
MODULE_DESCRIPTION = "Parse OpenAPI/Swagger collections, inspect REST endpoints, and run payload attacks on specific parameters."
MODULE_PREFIX = "/openapi"

module_bp = Blueprint('openapi_fuzzer', __name__, template_folder='../../templates', static_folder='../../static')


@module_bp.route('/')
def index():
    return render_template('openapi_fuzzer/index.html')


@module_bp.route('/api/parse', methods=['POST'])
def parse_spec():
    data = request.json
    spec_url = data.get('url')
    if not spec_url:
        return jsonify({"error": "No OpenAPI/Swagger URL provided"}), 400

    try:
        fuzzer = OpenAPIFuzzerCore(spec_url, data.get('headers', ''), data.get('auth'))
        result = fuzzer.parse()
        # Allow frontend to override base_url
        if data.get('base_url'):
            result['base_url'] = data['base_url'].rstrip('/')
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@module_bp.route('/api/attack_field', methods=['POST'])
def attack_field():
    data = request.json
    fuzzer = OpenAPIFuzzerCore(data.get('url'), data.get('headers', ''), data.get('auth'))
    if data.get('base_url'):
        fuzzer.base_url = data['base_url'].rstrip('/')
    results = fuzzer.attack_field(
        action=data.get('action'),
        target_field_name=data.get('target_field'),
        payloads=data.get('payloads', [])
    )
    return jsonify({"results": results})


@module_bp.route('/api/play_request', methods=['POST'])
def play_request():
    data = request.json
    fuzzer = OpenAPIFuzzerCore(data.get('url'), data.get('headers', ''), data.get('auth'))
    if data.get('base_url'):
        fuzzer.base_url = data['base_url'].rstrip('/')
    result = fuzzer.execute_request(
        action_data=data.get('action'),
        field_values=data.get('field_values', {})
    )
    # Wrap single result in array for frontend consistency
    return jsonify({"results": [result]})


@module_bp.route('/api/run_collection', methods=['POST'])
def run_collection():
    data = request.json
    fuzzer = OpenAPIFuzzerCore(data.get('url'), data.get('headers', ''), data.get('auth'))
    if data.get('base_url'):
        fuzzer.base_url = data['base_url'].rstrip('/')
    results = fuzzer.run_collection(
        actions=data.get('actions', []),
        payloads=data.get('payloads', [])
    )
    return jsonify({"results": results})