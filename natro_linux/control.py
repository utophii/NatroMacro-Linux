"""Single-instance control over a private Unix socket in XDG_RUNTIME_DIR."""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import socket
import socketserver
import threading


def runtime_dir():
    root = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    if not root.is_dir() or root.stat().st_uid != os.getuid():
        raise RuntimeError('A user-owned XDG_RUNTIME_DIR is required')
    path = root / 'natro-linux'
    if path.is_symlink():
        raise RuntimeError('Runtime directory must not be a symlink')
    path.mkdir(mode=0o700, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise RuntimeError('Runtime directory belongs to another user')
    path.chmod(0o700)
    return path


def acquire_instance_lock():
    fd = os.open(runtime_dir() / 'instance.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    lock = os.fdopen(fd, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('Natro Linux is already running')
    return lock


class ControlServer:
    def __init__(self, callback):
        self.path = runtime_dir() / 'control.sock'
        self.lock = acquire_instance_lock()
        if self.path.exists() or self.path.is_symlink():
            self.path.unlink()
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(1)
                request = self.rfile.readline(128).decode().strip()
                if request not in ('stop', 'pause', 'resume', 'status'):
                    result = {'error': 'Unknown control command'}
                else:
                    result = callback(request)
                self.wfile.write((json.dumps(result) + '\n').encode())
        try:
            self.server = socketserver.UnixStreamServer(str(self.path), Handler)
        except Exception:
            self.lock.close()
            raise
        self.path.chmod(0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.path.unlink(missing_ok=True)
        self.lock.close()


def request(action):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect(str(runtime_dir() / 'control.sock'))
        sock.sendall((action + '\n').encode())
        data = b''
        while b'\n' not in data and len(data) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return json.loads(data)
