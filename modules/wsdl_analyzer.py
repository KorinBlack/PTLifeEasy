import os
import tempfile
import json
import csv
import io
from flask import Blueprint, jsonify, request, render_template, Response
from classes.wsdl_analyzer import WsdlAnalyzer

MODULE_NAME = "WSDL Analyzer & Comparer"
MODULE_DESCRIPTION = "Meta-analysis on multiple WSDL files to find field mismatches and sensitive operations."
MODULE_PREFIX = "/wsdl_analyzer"

module_bp = Blueprint('wsdl_analyzer', __name__, template_folder='../../templates', static_folder='../../static')

@module_bp.route('/')
def index():
    return render_template('wsdl_analyzer/index.html')

@module_bp.route('/api/analyze', methods=['POST'])
def analyze():
    urls_raw = request.form.get('urls', '')
    sensitive_keywords_raw = request.form.get('sensitive_keywords', '')
    
    urls = [u.strip() for u in urls_raw.split('\n') if u.strip()]
    sensitive_keywords = [k.strip() for k in sensitive_keywords_raw.split(',') if k.strip()]
    
    if not sensitive_keywords:
        sensitive_keywords = None # Uses default in class
        
    sources = urls.copy()
    temp_files = []
    file_mapping = {}
    
    # Handle uploaded files
    if 'files' in request.files:
        for file in request.files.getlist('files'):
            if file.filename:
                fd, path = tempfile.mkstemp(suffix=".wsdl")
                with os.fdopen(fd, 'wb') as f:
                    file.save(f)
                temp_files.append(path)
                sources.append(path)
                file_mapping[path] = file.filename
                
    if not sources:
        return jsonify({"error": "No URLs or files provided"}), 400
        
    try:
        analyzer = WsdlAnalyzer(sensitive_keywords=sensitive_keywords)
        results = analyzer.analyze(sources)
        
        # Map temp files back to their original names in error messages
        if "errors" in results:
            cleaned_errors = {}
            for k, v in results["errors"].items():
                display_name = file_mapping.get(k, k)
                cleaned_errors[display_name] = v
            results["errors"] = cleaned_errors
            
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        for path in temp_files:
            try:
                os.remove(path)
            except:
                pass

@module_bp.route('/api/export', methods=['POST'])
def export():
    data = request.json
    results = data.get('results')
    format_type = data.get('format', 'json')
    
    if not results:
        return jsonify({"error": "No results to export"}), 400
        
    if format_type == 'json':
        response = Response(json.dumps(results, indent=4), mimetype='application/json')
        response.headers['Content-Disposition'] = 'attachment; filename=wsdl_analysis.json'
        return response
        
    elif format_type == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write Mismatches
        writer.writerow(["=== MISMATCHES ==="])
        writer.writerow(["Field Name", "Types", "Occurrences"])
        for m in results.get('mismatches', []):
            writer.writerow([m['name'], " | ".join(m['types']), m['occurrences']])
            
        writer.writerow([])
        # Write Sensitive Fields
        writer.writerow(["=== SENSITIVE FIELDS ==="])
        writer.writerow(["Field Name", "Types", "Occurrences"])
        for sf in results.get('sensitive_fields', []):
            writer.writerow([sf['name'], " | ".join(sf['types']), sf['occurrences']])
            
        writer.writerow([])
        # Write Sensitive Operations
        writer.writerow(["=== SENSITIVE OPERATIONS ==="])
        writer.writerow(["Operation Name", "Occurrences", "Is CRUD", "Is Upload/Download"])
        for so in results.get('sensitive_operations', []):
            writer.writerow([so['name'], so['occurrences'], so['is_crud'], so['is_upload_download']])
            
        response = Response(output.getvalue(), mimetype='text/csv')
        response.headers['Content-Disposition'] = 'attachment; filename=wsdl_analysis.csv'
        return response
        
    elif format_type == 'md':
        lines = ["# WSDL Analysis Report\n"]
        
        lines.append("## Mismatches")
        lines.append("| Field Name | Types | Occurrences |")
        lines.append("|---|---|---|")
        for m in results.get('mismatches', []):
            lines.append(f"| {m['name']} | {' | '.join(m['types'])} | {m['occurrences']} |")
            
        lines.append("\n## Sensitive Fields")
        lines.append("| Field Name | Types | Occurrences |")
        lines.append("|---|---|---|")
        for sf in results.get('sensitive_fields', []):
            lines.append(f"| {sf['name']} | {' | '.join(sf['types'])} | {sf['occurrences']} |")
            
        lines.append("\n## Sensitive Operations")
        lines.append("| Operation Name | Occurrences | Is CRUD | Is Upload/Download |")
        lines.append("|---|---|---|---|")
        for so in results.get('sensitive_operations', []):
            lines.append(f"| {so['name']} | {so['occurrences']} | {so['is_crud']} | {so['is_upload_download']} |")
            
        response = Response("\n".join(lines), mimetype='text/markdown')
        response.headers['Content-Disposition'] = 'attachment; filename=wsdl_analysis.md'
        return response
        
    return jsonify({"error": "Invalid format"}), 400
