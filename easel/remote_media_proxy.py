"""A bounded public-network proxy for downloader subprocesses."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import selectors
import socket
import threading
import time
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

from easel._public_network import RemoteMediaError, connect_public, validate_public_url


@dataclass(slots=True)
class _TransferBudget:
    remaining: int
    deadline: float
    lock: threading.Lock = field(default_factory=threading.Lock)
    active: set[socket.socket] = field(default_factory=set)

    def charge(self, count: int) -> None:
        with self.lock:
            self.remaining -= count
            if self.remaining < 0 or time.monotonic() >= self.deadline:
                raise RemoteMediaError('参考视频传输超过大小或时长限制')

    def close(self) -> None:
        with self.lock:
            streams = tuple(self.active)
            self.active.clear()
        for stream in streams:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            stream.close()


def _relay(client: socket.socket, upstream: socket.socket, budget: _TransferBudget) -> None:
    with selectors.DefaultSelector() as selector:
        selector.register(client, selectors.EVENT_READ, upstream)
        selector.register(upstream, selectors.EVENT_READ, client)
        while time.monotonic() < budget.deadline:
            for event, _mask in selector.select(timeout=min(1.0, max(0.01, budget.deadline-time.monotonic()))):
                body = event.fileobj.recv(64 * 1024)
                if not body:
                    return
                budget.charge(len(body))
                event.data.sendall(body)


def _handler(budget: _TransferBudget, authorization: str) -> type[BaseHTTPRequestHandler]:
    class PublicProxyHandler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, _format, *_args) -> None:
            return

        def _authorize(self) -> bool:
            supplied = self.headers.get('Proxy-Authorization', '')
            if not secrets.compare_digest(supplied, authorization):
                self.send_error(407, 'Proxy authentication required')
                return False
            return True

        def _forward(self, host: str, port: int, prefix: bytes = b'') -> None:
            upstream = connect_public(host, port, min(30.0, max(0.01, budget.deadline-time.monotonic())))
            with upstream:
                with budget.lock:
                    budget.active.add(upstream)
                    budget.active.add(self.connection)
                try:
                    if prefix:
                        upstream.sendall(prefix)
                    else:
                        self.send_response(200, 'Connection established')
                        self.end_headers()
                    _relay(self.connection, upstream, budget)
                finally:
                    with budget.lock:
                        budget.active.discard(upstream)
                        budget.active.discard(self.connection)
            self.close_connection = True

        def do_CONNECT(self) -> None:
            if not self._authorize():
                return
            try:
                parsed = urlsplit('https://' + self.path)
                if not parsed.hostname or parsed.path or parsed.username or parsed.password:
                    raise RemoteMediaError('Invalid CONNECT target')
                self._forward(parsed.hostname, parsed.port or 443)
            except (RemoteMediaError, OSError, ValueError):
                self.close_connection = True

        def do_GET(self) -> None:
            if not self._authorize():
                return
            try:
                parsed = urlsplit(validate_public_url(self.path))
                if parsed.scheme != 'http':
                    raise RemoteMediaError('HTTP proxy requires an HTTP target')
                target = urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
                headers = [f'{self.command} {target} HTTP/1.1', f'Host: {parsed.netloc}', 'Connection: close']
                for name, value in self.headers.items():
                    if name.lower() not in {'proxy-authorization', 'proxy-connection', 'connection', 'host'}:
                        headers.append(f'{name}: {value}')
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > 4 * 1024**2 or self.headers.get('Transfer-Encoding'):
                    raise RemoteMediaError('Unsupported proxy request body')
                body = self.rfile.read(length) if length else b''
                budget.charge(len(body))
                prefix = ('\r\n'.join(headers)+'\r\n\r\n').encode('latin-1') + body
                self._forward(parsed.hostname, parsed.port or 80, prefix)
            except (RemoteMediaError, OSError, ValueError):
                self.close_connection = True

        do_HEAD = do_GET
        do_POST = do_GET

    return PublicProxyHandler


@contextmanager
def public_download_proxy(*, max_bytes: int, timeout: float) -> Iterator[str]:
    """Confine every HTTP(S) downloader request to validated numeric public IPs."""
    import base64
    password = secrets.token_urlsafe(32)
    authorization = 'Basic '+base64.b64encode(f'easel:{password}'.encode()).decode()
    budget = _TransferBudget(max_bytes, time.monotonic()+timeout)
    with ThreadingHTTPServer(('127.0.0.1', 0), _handler(budget, authorization)) as server:
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://easel:{password}@127.0.0.1:{server.server_port}'
        finally:
            budget.close()
            server.shutdown()
            thread.join(timeout=2)
