from pathlib import Path
import socket

import pytest

from easel.remote_media import RemoteMediaError, download_to_file, validate_public_url
from easel._public_network import connect_public


def _dns(address: str, port: int = 0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]


@pytest.mark.parametrize("url", ["file:///tmp/x", "ftp://example.com/x", "https://u:p@example.com/x"])
def test_remote_media_rejects_unsafe_url(url):
    with pytest.raises(RemoteMediaError):
        validate_public_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.1.2", "::1"])
def test_remote_media_rejects_non_public_dns(monkeypatch, address):
    monkeypatch.setattr("easel.remote_media.socket.getaddrinfo", lambda *args, **kwargs: _dns(address))
    with pytest.raises(RemoteMediaError):
        validate_public_url("https://media.example/x.mp4")


def test_download_to_file_enforces_stream_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("easel.remote_media.socket.getaddrinfo", lambda *args, **kwargs: _dns("93.184.216.34"))

    class Response:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _size): return b"too-large"

    class Opener:
        def open(self, *_args, **_kwargs): return Response()

    monkeypatch.setattr("easel.remote_media.request.build_opener", lambda *_: Opener())
    with pytest.raises(RemoteMediaError):
        download_to_file("https://media.example/x.mp4", Path(tmp_path) / "x.mp4", max_bytes=4)


def test_actual_connection_uses_resolved_numeric_address_without_second_dns(monkeypatch):
    calls = []
    monkeypatch.setattr('easel._public_network.socket.getaddrinfo', lambda *_args, **_kwargs: _dns('93.184.216.34', 443))

    class Stream:
        def settimeout(self, timeout):
            return
        def connect(self, address):
            calls.append(address)
        def close(self):
            return

    stream = Stream()
    monkeypatch.setattr('easel._public_network.socket.socket', lambda *_args: stream)
    assert connect_public('media.example', 443, 5) is stream
    assert calls == [('93.184.216.34', 443)]


def test_download_rejects_dns_rebinding_before_opening_socket(tmp_path, monkeypatch):
    answers = iter([_dns('93.184.216.34'), _dns('127.0.0.1')])
    monkeypatch.setattr('easel._public_network.socket.getaddrinfo', lambda *_args, **_kwargs: next(answers))
    def forbidden_socket(*_args, **_kwargs):
        pytest.fail('a DNS-rebound private address must be rejected before a socket is created')
    monkeypatch.setattr('easel._public_network.socket.socket', forbidden_socket)
    with pytest.raises(RemoteMediaError):
        download_to_file('http://media.example/video.mp4', tmp_path / 'video.mp4', timeout=1)
    assert not (tmp_path / 'video.mp4').exists()


def test_redirect_revalidates_target_through_actual_http_handler(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from easel import _public_network

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', 'http://127.0.0.1/private')
            self.end_headers()
        def log_message(self, *_args):
            return

    original = _public_network.resolve_public
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def resolve(host, port):
            if host == 'fixture.example':
                return (_public_network.PublicAddress(socket.AF_INET, socket.SOCK_STREAM, 6,
                                                       ('127.0.0.1', server.server_port)),)
            return original(host, port)
        monkeypatch.setattr(_public_network, 'resolve_public', resolve)
        try:
            with pytest.raises(RemoteMediaError):
                download_to_file('http://fixture.example/video', tmp_path / 'video.mp4')
        finally:
            server.shutdown()
            thread.join(timeout=2)
    assert not (tmp_path / 'video.mp4').exists()
