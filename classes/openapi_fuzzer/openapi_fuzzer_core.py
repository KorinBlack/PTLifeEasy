import json
import time
import requests
import urllib3
import yaml
from urllib.parse import urljoin
from utils import load_config

# Suppress insecure request warnings for proxy usage
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class OpenAPIFuzzerCore:
    """Parser & Fuzzer for OpenAPI / Swagger collections.

    Mirrors the WSDL fuzzer workflow but operates on REST endpoints described
    by an OpenAPI (3.x) or Swagger (2.x) document (JSON or YAML, URL or file).
    """

    # In-memory cache of parsed specs keyed by spec_url, so that repeated
    # requests (play_request / attack_field / run_collection) don't re-fetch
    # the spec document over HTTP every time.
    _spec_cache = {}

    def __init__(self, spec_url, custom_headers_input="", auth_config=None):
        self.spec_url = spec_url
        self.custom_headers_input = custom_headers_input
        self.header_dict = self._parse_custom_headers(custom_headers_input)
        self.auth_config = auth_config or {}
        self.session = self._create_session()
        self.spec = None
        self.openapi_version = None
        self.base_url = ""

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

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
            if config.get('proxy_http'):
                proxies['http'] = config['proxy_http']
            if config.get('proxy_https'):
                proxies['https'] = config['proxy_https']
            session.proxies = proxies

        session.headers.update(self.header_dict)
        return session

    # ------------------------------------------------------------------
    # Spec loading & parsing
    # ------------------------------------------------------------------

    def _load_spec(self):
        """Fetch the OpenAPI/Swagger document (JSON or YAML) from URL.

        Uses an in-memory cache keyed by spec_url to avoid re-fetching the
        document on every play_request / attack_field / run_collection call.
        """
        cached = OpenAPIFuzzerCore._spec_cache.get(self.spec_url)
        if cached is not None:
            self.spec = cached
            self.openapi_version = cached.get('swagger', cached.get('openapi', 'unknown'))
            self.base_url = self._resolve_base_url()
            return cached

        res = self.session.get(self.spec_url, timeout=15, verify=False)
        res.raise_for_status()
        text = res.text

        # Try JSON first, fall back to YAML
        try:
            spec = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            spec = yaml.safe_load(text)

        if not isinstance(spec, dict):
            raise ValueError("Invalid OpenAPI/Swagger document: expected a JSON/YAML object.")

        self.spec = spec
        self.openapi_version = spec.get('swagger', spec.get('openapi', 'unknown'))

        self.base_url = self._resolve_base_url()
        # Cache the parsed spec for reuse across instances
        OpenAPIFuzzerCore._spec_cache[self.spec_url] = spec
        return spec

    def _resolve_base_url(self):
        """Resolve the base URL from servers (OpenAPI 3) or host+basePath (Swagger 2).

        Falls back to the scheme://host:port root of the spec URL itself,
        NOT the directory containing the spec file.
        """
        spec = self.spec

        # OpenAPI 3: servers list
        servers = spec.get('servers', [])
        if servers:
            first = servers[0].get('url', '')
            if first:
                return first.rstrip('/')

        # Swagger 2: host + basePath + schemes
        host = spec.get('host', '')
        base_path = spec.get('basePath', '') or ''
        schemes = spec.get('schemes', ['http'])
        if host:
            scheme = schemes[0] if schemes else 'http'
            return f"{scheme}://{host}{base_path}".rstrip('/')

        # Fallback: derive root from the spec URL (scheme://host:port only)
        try:
            from urllib.parse import urlparse
            parsed = urlparse(self.spec_url)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            pass

        return self.spec_url.rstrip('/')

    def _extract_fields(self, parameters):
        """Convert a list of OpenAPI parameters into the field model used by the UI."""
        fields = []
        for param in parameters or []:
            name = param.get('name', '')
            if not name:
                continue
            schema = param.get('schema', {})
            p_type = param.get('type') or schema.get('type', 'string')
            default = param.get('default', schema.get('default', '?'))
            fields.append({
                "name": name,
                "in": param.get('in', 'query'),
                "type": p_type,
                "required": bool(param.get('required', False)),
                "active": True,
                "value": default if default is not None else "?"
            })
        return fields

    def _extract_body_fields(self, body_schema, prefix=""):
        """Recursively extract fields from a request body schema (OpenAPI 3 / Swagger 2)."""
        fields = []
        if not body_schema:
            return fields

        # Resolve $ref (very lightweight local resolver)
        schema = self._resolve_ref(body_schema)

        properties = schema.get('properties', {})
        for name, prop in properties.items():
            full_name = f"{prefix}.{name}" if prefix else name
            prop = self._resolve_ref(prop)
            p_type = prop.get('type', 'object')

            if p_type == 'object' and prop.get('properties'):
                fields.extend(self._extract_body_fields(prop, full_name))
            elif p_type == 'array':
                item_fields = self._extract_body_fields(prop.get('items', {}), full_name + '[]')
                if item_fields:
                    fields.extend(item_fields)
                else:
                    fields.append({
                        "name": full_name,
                        "in": "body",
                        "type": "array",
                        "required": False,
                        "active": True,
                        "value": "?"
                    })
            else:
                fields.append({
                    "name": full_name,
                    "in": "body",
                    "type": p_type,
                    "required": False,
                    "active": True,
                    "value": prop.get('default', '?')
                })
        return fields

    def _resolve_ref(self, node):
        """Resolve a local $ref like '#/components/schemas/Pet' inside the spec."""
        if not isinstance(node, dict):
            return node
        ref = node.get('$ref')
        if not ref:
            return node
        try:
            if ref.startswith('#/'):
                parts = ref.lstrip('#/').split('/')
                target = self.spec
                for part in parts:
                    target = target.get(part, {})
                return target if target else node
        except Exception:
            pass
        return node

    def _get_body_schema(self, operation):
        """Extract the request body schema for both OpenAPI 3 and Swagger 2."""
        # OpenAPI 3
        body = operation.get('requestBody')
        if body:
            content = body.get('content', {})
            for media_type in ('application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'):
                media = content.get(media_type)
                if media and media.get('schema'):
                    return media['schema'], media_type
            # Take the first available media type
            for media in content.values():
                if media.get('schema'):
                    return media['schema'], 'application/json'
            return None, None

        # Swagger 2: body parameter
        for param in operation.get('parameters', []):
            if param.get('in') == 'body' and param.get('schema'):
                return param['schema'], 'application/json'

        return None, None

    def parse(self):
        """Parse the spec and return the list of operations (actions)."""
        spec = self._load_spec()
        paths = spec.get('paths', {})

        actions = []
        for path, path_item in paths.items():
            path_item = self._resolve_ref(path_item)
            for method in ('get', 'post', 'put', 'patch', 'delete', 'head', 'options'):
                operation = path_item.get(method)
                if not operation:
                    continue

                # Path + query + header + cookie parameters (Swagger 2 also defines them at path level)
                parameters = list(path_item.get('parameters', [])) + list(operation.get('parameters', []))
                fields = self._extract_fields(parameters)

                # Request body fields
                body_schema, media_type = self._get_body_schema(operation)
                if body_schema:
                    body_fields = self._extract_body_fields(body_schema)
                    fields.extend(body_fields)

                actions.append({
                    "name": operation.get('operationId') or f"{method.upper()} {path}",
                    "method": method.upper(),
                    "path": path,
                    "service": spec.get('info', {}).get('title', 'unknown'),
                    "active": True,
                    "fields": fields,
                    "body_media_type": media_type,
                    "summary": operation.get('summary', '')
                })

        return {
            "actions": actions,
            "base_url": self.base_url,
            "openapi_version": self.openapi_version,
            "title": spec.get('info', {}).get('title', 'OpenAPI Collection')
        }

    # ------------------------------------------------------------------
    # Request execution
    # ------------------------------------------------------------------

    def _ensure_loaded(self):
        """Ensure base_url is available for request execution.

        Endpoints like play_request / attack_field / run_collection create a
        fresh instance without calling parse(). The frontend already passes
        the resolved base_url, so we only need to load the spec when base_url
        is still empty (e.g. direct API call without base_url). The spec is
        served from the in-memory cache when available, so no extra HTTP
        request is made on repeated calls.
        """
        if not self.base_url:
            # base_url missing: either first call or caller didn't provide it.
            # Use the cached spec if available to avoid an HTTP fetch.
            if self.spec is None and self.spec_url in OpenAPIFuzzerCore._spec_cache:
                self.spec = OpenAPIFuzzerCore._spec_cache[self.spec_url]
                self.openapi_version = self.spec.get('swagger', self.spec.get('openapi', 'unknown'))
                self.base_url = self._resolve_base_url()
            elif self.spec is None:
                self._load_spec()

    def _unflatten_dict(self, flat_dict):
        result = {}
        for key, value in flat_dict.items():
            parts = key.replace('[]', '').split('.')
            d = result
            for part in parts[:-1]:
                if part not in d or not isinstance(d[part], dict):
                    d[part] = {}
                d = d[part]
            d[parts[-1]] = value
        return result

    def _build_url(self, action, field_values):
        """Build the final URL substituting path params and appending query params.

        If the action has override_base_url enabled, use that instead of self.base_url.
        """
        base = self.base_url
        if action.get('override_base_url') and action.get('custom_base_url'):
            base = action['custom_base_url'].rstrip('/')
        url = urljoin(base + '/', action['path'].lstrip('/'))

        # Path parameters substitution
        for field in action.get('fields', []):
            if field.get('in') == 'path':
                val = field_values.get(field['name'], field['value'])
                url = url.replace('{' + field['name'] + '}', str(val))

        # Query parameters
        query_params = {}
        for field in action.get('fields', []):
            if field.get('in') == 'query' and field.get('active'):
                query_params[field['name']] = field_values.get(field['name'], field['value'])

        return url, query_params

    def _build_auth_header(self):
        """Build an Authorization header from the auth_config dict.

        Supported types:
          - none   -> no auth header
          - basic  -> base64(user:pass)
          - bearer -> Bearer <token>
          - apikey -> custom header name + value
        """
        auth = self.auth_config
        if not auth:
            return {}

        auth_type = auth.get('type', 'none')
        if auth_type == 'none':
            return {}

        if auth_type == 'basic':
            import base64
            user = auth.get('username', '')
            pwd = auth.get('password', '')
            token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            return {"Authorization": f"Basic {token}"}

        if auth_type == 'bearer':
            token = auth.get('token', '')
            if token:
                return {"Authorization": f"Bearer {token}"}
            return {}

        if auth_type == 'apikey':
            key_name = auth.get('key_name', 'X-API-Key')
            key_value = auth.get('key_value', '')
            if key_value:
                return {key_name: key_value}
            return {}

        return {}

    def _build_headers(self, action, field_values):
        headers = {}
        config = load_config()
        if config.get('custom_headers'):
            headers.update(config['custom_headers'])
        if self.header_dict:
            headers.update(self.header_dict)

        # Auth header (global)
        headers.update(self._build_auth_header())

        # Per-action auth override
        action_auth = action.get('auth_config')
        if action_auth:
            saved = self.auth_config
            self.auth_config = action_auth
            # Remove the global Authorization/apikey header first so the override wins
            if action_auth.get('type') in ('basic', 'bearer'):
                headers.pop('Authorization', None)
            elif action_auth.get('type') == 'apikey':
                headers.pop(action_auth.get('key_name', 'X-API-Key'), None)
            headers.update(self._build_auth_header())
            self.auth_config = saved

        # Header parameters
        for field in action.get('fields', []):
            if field.get('in') == 'header' and field.get('active'):
                headers[field['name']] = str(field_values.get(field['name'], field['value']))

        media = action.get('body_media_type')
        body_fields = [f for f in action.get('fields', []) if f.get('in') == 'body' and f.get('active')]
        if body_fields and media:
            headers['Content-Type'] = media

        return headers

    def _build_body(self, action, field_values):
        body_fields = [f for f in action.get('fields', []) if f.get('in') == 'body' and f.get('active')]
        if not body_fields:
            return None

        flat = {}
        for f in body_fields:
            flat[f['name']] = field_values.get(f['name'], f['value'])

        nested = self._unflatten_dict(flat)
        media = action.get('body_media_type', 'application/json')

        if media == 'application/json':
            return json.dumps(nested)
        elif media in ('application/x-www-form-urlencoded', 'multipart/form-data'):
            # Flatten back for form data
            flat_vals = {}
            for f in body_fields:
                flat_vals[f['name']] = str(field_values.get(f['name'], f['value']))
            return flat_vals
        return json.dumps(nested)

    def execute_request(self, action_data, field_values):
        """Execute a single HTTP request for the given action."""
        self._ensure_loaded()
        url, query_params = self._build_url(action_data, field_values)
        headers = self._build_headers(action_data, field_values)
        body = self._build_body(action_data, field_values)
        method = action_data['method']

        config = load_config()
        proxies = {}
        if config.get('proxy_enabled'):
            if config.get('proxy_http'):
                proxies['http'] = config['proxy_http']
            if config.get('proxy_https'):
                proxies['https'] = config['proxy_https']

        start_time = time.time()
        try:
            kwargs = {
                'headers': headers,
                'proxies': proxies,
                'verify': False,
                'timeout': 10
            }
            if query_params:
                kwargs['params'] = query_params
            if body is not None:
                if isinstance(body, dict):
                    kwargs['data'] = body
                else:
                    kwargs['data'] = body

            res = self.session.request(method, url, **kwargs)
            elapsed = int((time.time() - start_time) * 1000)

            return {
                "status": res.status_code,
                "length": len(res.content),
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

    # ------------------------------------------------------------------
    # Attack strategies
    # ------------------------------------------------------------------

    def attack_field(self, action, target_field_name, payloads):
        """Inject payloads into a single field, keeping the others at base value."""
        results = []
        base_values = {f['name']: f['value'] for f in action['fields']}

        for payload in payloads:
            if not payload.strip():
                continue
            current_values = base_values.copy()
            current_values[target_field_name] = payload

            res = self.execute_request(action, current_values)
            res['payload'] = payload
            results.append(res)

        return results

    def run_collection(self, actions, payloads):
        """Run the full matrix: every active field of every active action x payloads."""
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

                    res = self.execute_request(action, current_values)
                    res['action_name'] = action['name']
                    res['field_name'] = field['name']
                    res['payload'] = payload
                    all_results.append(res)

        return all_results