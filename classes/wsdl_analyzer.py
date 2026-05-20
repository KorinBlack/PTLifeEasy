import zeep
import urllib3
import requests
import re
from collections import defaultdict
from utils import load_config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class WsdlAnalyzer:
    def __init__(self, sensitive_keywords=None):
        self.settings = zeep.Settings(strict=False, xml_huge_tree=True)
        if not sensitive_keywords:
            self.sensitive_keywords = ["password", "token", "secret", "key", "auth", "credential"]
        else:
            self.sensitive_keywords = sensitive_keywords
            
        self.upload_download_keywords = ["upload", "download", "file", "document", "attachment", "retrieve"]
        self.crud_keywords = ["create", "read", "update", "delete", "insert", "remove", "add", "edit"]

    def _create_session(self):
        session = requests.Session()
        session.verify = False
        config = load_config()
        if config.get('proxy_enabled'):
            proxies = {}
            if config.get('proxy_http'): proxies['http'] = config['proxy_http']
            if config.get('proxy_https'): proxies['https'] = config['proxy_https']
            session.proxies = proxies
        
        custom_headers = config.get('custom_headers', {})
        if custom_headers:
            session.headers.update(custom_headers)
        return session

    def _is_sensitive(self, text, keywords):
        text_lower = text.lower()
        for kw in keywords:
            if kw.lower() in text_lower:
                return True
        return False

    def _extract_fields_recursive(self, elements, prefix=""):
        fields = []
        for name, element in elements:
            full_name = f"{prefix}.{name}" if prefix else name
            try:
                if hasattr(element, 'type') and hasattr(element.type, 'elements') and element.type.elements:
                    fields.extend(self._extract_fields_recursive(element.type.elements, full_name))
                else:
                    t_name = getattr(element.type, 'name', str(element.type)) if hasattr(element, 'type') else "unknown"
                    fields.append({
                        "name": full_name,
                        "type": t_name or "unknown"
                    })
            except Exception:
                t_name = getattr(element.type, 'name', str(element.type)) if hasattr(element, 'type') else "unknown"
                fields.append({
                    "name": full_name,
                    "type": t_name or "unknown"
                })
        return fields

    def analyze(self, sources):
        field_stats = defaultdict(lambda: {"types": set(), "occurrences": 0})
        op_stats = defaultdict(lambda: {"occurrences": 0})
        errors = {}
        
        session = self._create_session()
        transport = zeep.transports.Transport(session=session)

        for source in sources:
            try:
                client = zeep.Client(source, settings=self.settings, transport=transport)
                for service in client.wsdl.services.values():
                    for port in service.ports.values():
                        for op_name, operation in port.binding._operations.items():
                            op_stats[op_name]["occurrences"] += 1
                            
                            try:
                                if hasattr(operation, 'input') and operation.input:
                                    parsed = False
                                    if hasattr(operation.input, 'body') and hasattr(operation.input.body, 'type') and hasattr(operation.input.body.type, 'elements'):
                                        try:
                                            fields = self._extract_fields_recursive(operation.input.body.type.elements)
                                            for f in fields:
                                                field_stats[f["name"]]["types"].add(f["type"])
                                                field_stats[f["name"]]["occurrences"] += 1
                                            parsed = True
                                        except Exception:
                                            pass
                                            
                                    if not parsed:
                                        try:
                                            signature = operation.input.signature()
                                            if signature:
                                                params = signature.split(', ')
                                                for p in params:
                                                    if ':' in p:
                                                        name, t = p.split(':', 1)
                                                        name = name.strip()
                                                        field_stats[name]["types"].add(t.strip())
                                                        field_stats[name]["occurrences"] += 1
                                        except Exception:
                                            pass
                            except Exception:
                                pass
            except Exception as e:
                print(f"Error parsing {source}: {e}")
                errors[source] = str(e)

        # Post processing
        fields_result = []
        for name, data in field_stats.items():
            fields_result.append({
                "name": name,
                "types": list(data["types"]),
                "occurrences": data["occurrences"],
                "is_sensitive": self._is_sensitive(name, self.sensitive_keywords),
                "has_mismatch": len(data["types"]) > 1
            })

        ops_result = []
        for name, data in op_stats.items():
            ops_result.append({
                "name": name,
                "occurrences": data["occurrences"],
                "is_sensitive": self._is_sensitive(name, self.sensitive_keywords),
                "is_crud": self._is_sensitive(name, self.crud_keywords),
                "is_upload_download": self._is_sensitive(name, self.upload_download_keywords)
            })
            
        mismatches = [f for f in fields_result if f["has_mismatch"]]
        sensitive_fields = [f for f in fields_result if f["is_sensitive"]]
        sensitive_ops = [o for o in ops_result if o["is_sensitive"] or o["is_crud"] or o["is_upload_download"]]

        return {
            "fields": sorted(fields_result, key=lambda x: x["name"]),
            "operations": sorted(ops_result, key=lambda x: x["name"]),
            "mismatches": sorted(mismatches, key=lambda x: x["name"]),
            "sensitive_fields": sorted(sensitive_fields, key=lambda x: x["name"]),
            "sensitive_operations": sorted(sensitive_ops, key=lambda x: x["name"]),
            "errors": errors
        }
