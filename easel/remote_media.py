"""Bounded downloads from public HTTP(S) media URLs."""
from __future__ import annotations

import http.client
import os
from pathlib import Path
import socket
import tempfile
from urllib import error, request
import time
from easel._public_network import RemoteMediaError, connect_public, validate_public_url


MAX_REDIRECTS = 3
DEFAULT_MAX_BYTES = 2 * 1024**3


class _PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = connect_public(self.host, self.port, self.timeout)


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        stream = connect_public(self.host, self.port, self.timeout)
        try:
            self.sock = self._context.wrap_socket(stream, server_hostname=self.host)
        except (OSError, ValueError):
            stream.close()
            raise


class _PublicHTTPHandler(request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


class _RedirectHandler(request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > MAX_REDIRECTS:
            raise RemoteMediaError("媒体 URL 重定向次数超过限制")
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_opener(redirect_handler: request.HTTPRedirectHandler | None = None) -> request.OpenerDirector:
    """Create an HTTP(S) opener whose actual socket destinations are public."""
    return request.build_opener(request.ProxyHandler({}), _PublicHTTPHandler(),
                                _PublicHTTPSHandler(), redirect_handler or _RedirectHandler())


def download_to_file(url: str, destination: Path, *, max_bytes: int = DEFAULT_MAX_BYTES,
                     timeout: float = 120.0, headers: dict[str, str] | None = None,
                     direct: bool = False) -> int:
    """Stream a bounded public download through a temporary sibling file."""
    if max_bytes <= 0 or timeout <= 0:
        raise ValueError("max_bytes 和 timeout 必须为正数")
    source = validate_public_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A proxy may resolve a hostname differently from this host. Direct pinned
    # sockets keep the public-address guarantee at the actual connection boundary.
    opener = public_opener()
    req = request.Request(source, headers=headers or {})
    temporary: Path | None = None
    deadline = time.monotonic() + timeout
    try:
        with opener.open(req, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length:
                try:
                    declared = int(length)
                except ValueError as exc:
                    raise RemoteMediaError("媒体响应大小无效") from exc
                if declared > max_bytes:
                    raise RemoteMediaError("媒体超过大小上限")
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
                total = 0
                while True:
                    if time.monotonic() >= deadline:
                        raise RemoteMediaError('媒体下载超过总时限')
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise RemoteMediaError("媒体超过大小上限")
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
        return total
    except error.HTTPError as exc:
        exc.close()
        raise RemoteMediaError("媒体下载失败") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise RemoteMediaError("媒体下载失败") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
