import traceback
import requests
import urllib3
import zeep
import time
from utils import load_config

# Suppress insecure request warnings for proxy usage
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class WSDLFuzzerCore:
    def __init__(self, wsdl_url, custom_headers_input=""):
        self.wsdl_url = wsdl_url
        self.custom_headers_input = custom_headers_input
        self.header_dict = self._parse_custom_headers(custom_headers_input)
        self.session = self._create_session()
        self.transport = zeep.transports.Transport(session=self.session)
        self.settings = zeep.Settings(strict=False, xml_huge_tree=True)
        self.client = None

    def _parse_custom_headers(self, headers_str):
        header_dict = {}
        if headers_str:
            for line in headers_str.split('\n'):
                if ':' in line:
                    k, v = line.split(':', 1)
                    header_dict[k.strip()] = v.strip()
        return header_dict

    def _create_session(self):
        session = requests.Session()
        session.verify = False
        
        config = load_config()
        if config.get('proxy_enabled'):
            proxies = {}
            if config.get('proxy_http'): proxies['http'] = config['proxy_http']
            if config.get('proxy_https'): proxies['https'] = config['proxy_https']
            session.proxies = proxies
            
        session.headers.update(self.header_dict)
        return session

    def initialize_client(self):
        if not self.client:
            self.client = zeep.Client(self.wsdl_url, settings=self.settings, transport=self.transport)

    def _extract_fields_recursive(self, elements, prefix=""):
        fields = []
        for name, element in elements:
            full_name = f"{prefix}.{name}" if prefix else name
            
            try:
                # If it's a complex type with nested elements
                if hasattr(element, 'type') and hasattr(element.type, 'elements') and element.type.elements:
                    fields.extend(self._extract_fields_recursive(element.type.elements, full_name))
                else:
                    t_name = getattr(element.type, 'name', str(element.type)) if hasattr(element, 'type') else "unknown"
                    fields.append({
                        "name": full_name,
                        "type": t_name or "unknown",
                        "active": True,
                        "value": "?"
                    })
            except Exception as e:
                # Fallback if something goes wrong during recursion
                print(f"Error parsing nested element {full_name}: {e}")
                t_name = getattr(element.type, 'name', str(element.type)) if hasattr(element, 'type') else "unknown"
                fields.append({
                    "name": full_name,
                    "type": t_name or "unknown",
                    "active": True,
                    "value": "?"
                })
        return fields

    def parse(self):
        self.initialize_client()
        
        target_namespace = self.client.namespaces.get('tns', '')
        if not target_namespace:
            try:
                if hasattr(self.client.wsdl, 'target_namespace'):
                    target_namespace = self.client.wsdl.target_namespace
                elif hasattr(self.client.wsdl, 'port_types'):
                    for pt in self.client.wsdl.port_types.values():
                        if hasattr(pt, 'name') and hasattr(pt.name, 'namespace'):
                            target_namespace = pt.name.namespace
                            if target_namespace:
                                break
                    target_namespace = self.client.wsdl.target_namespace
            except:
                pass

        actions = []
        for service in self.client.wsdl.services.values():
            for port in service.ports.values():
                for op_name, operation in port.binding._operations.items():
                    fields = []
                    try:
                        if hasattr(operation, 'input') and operation.input:
                            parsed = False
                            # Try the recursive elements approach first for better accuracy
                            if hasattr(operation.input, 'body') and hasattr(operation.input.body, 'type') and hasattr(operation.input.body.type, 'elements'):
                                try:
                                    fields = self._extract_fields_recursive(operation.input.body.type.elements)
                                    parsed = True
                                except Exception as e:
                                    print(f"Error parsing elements for {op_name}: {e}")
                            
                            # Fallback to signature parsing
                            if not parsed:
                                try:
                                    signature = operation.input.signature()
                                    if signature:
                                        params = signature.split(', ')
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
                    except Exception as e:
                        print(f"Error parsing operation {op_name}: {e}")
                        
                    actions.append({
                        "name": op_name,
                        "service": service.name,
                        "port": port.name,
                        "active": True,
                        "fields": fields,
                        "target_namespace": target_namespace,
                        "soap_action": operation.soapaction or ""
                    })
                    
        return {"actions": actions, "target_namespace": target_namespace}

    def _unflatten_dict(self, flat_dict):
        result = {}
        for key, value in flat_dict.items():
            parts = key.split('.')
            d = result
            for part in parts[:-1]:
                if part not in d:
                    d[part] = {}
                d = d[part]
            d[parts[-1]] = value
        return result

    def _dict_to_xml(self, d, indent=2):
        xml_str = ""
        spaces = " " * indent
        for k, v in d.items():
            if isinstance(v, dict):
                xml_str += f"{spaces}<{k}>\n"
                xml_str += self._dict_to_xml(v, indent + 2)
                xml_str += f"{spaces}</{k}>\n"
            else:
                xml_str += f"{spaces}<{k}>{v}</{k}>\n"
        return xml_str

    def execute_request(self, action_data, field_values, target_namespace, xxe_payload=""):
        op_name = action_data['name']
        soap_action = action_data.get('soap_action', '')
        
        # Build nested structure from flat dot-notated field names
        flat_fields = {}
        for field in action_data.get('fields', []):
            if field['active']:
                val = field_values.get(field['name'], field['value'])
                flat_fields[field['name']] = val
                
        nested_fields = self._unflatten_dict(flat_fields)
        xml_fields = self._dict_to_xml(nested_fields, indent=6).rstrip('\n')
        
        # Add newline before fields if there are any to make it look clean
        if xml_fields:
            xml_fields = "\n" + xml_fields + "\n    "
                
        envelope = f"""<?xml version="1.0" encoding="utf-8"?>
{xxe_payload}
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:srv="{target_namespace}" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <srv:{op_name}>{xml_fields}</srv:{op_name}>
  </soap:Body>
</soap:Envelope>"""

        config = load_config()
        proxies = {}
        if config.get('proxy_enabled'):
            if config.get('proxy_http'): proxies['http'] = config['proxy_http']
            if config.get('proxy_https'): proxies['https'] = config['proxy_https']
        

        action_val = soap_action if soap_action else ""
        headers = {
            'Content-Type': 'text/xml; charset=utf-8',
            'SOAPAction': f'{action_val}'
        }
        
        if config.get('custom_headers'):
            headers.update(config['custom_headers'])
        if self.header_dict:
            headers.update(self.header_dict)
            
        start_time = time.time()
        try:
            endpoint = self.wsdl_url.split('?')[0] if '?' in self.wsdl_url else self.wsdl_url
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

    def attack_field(self, action, target_field_name, payloads, target_namespace):
        results = []
        base_values = {f['name']: f['value'] for f in action['fields']}
        
        for payload in payloads:
            if not payload.strip():
                continue
                
            current_values = base_values.copy()
            current_values[target_field_name] = payload
            
            res = self.execute_request(action, current_values, target_namespace)
            res['payload'] = payload
            results.append(res)
            
        return results

    def run_collection(self, actions, payloads, target_namespace):
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
                    
                    res = self.execute_request(action, current_values, target_namespace)
                    res['action_name'] = action['name']
                    res['field_name'] = field['name']
                    res['payload'] = payload
                    all_results.append(res)
                    
        return all_results

    def attack_xxe(self, action, payloads, target_namespace):
        results = []
        base_values = {f['name']: f['value'] for f in action.get('fields', [])}
        
        for payload in payloads:
            if not payload.strip():
                continue
                
            res = self.execute_request(action, base_values, target_namespace, xxe_payload=payload)
            res['payload'] = payload
            results.append(res)
            
        return results
