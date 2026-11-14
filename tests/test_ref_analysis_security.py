import importlib.util
import socket
from pathlib import Path

import pytest


MODULE = Path(__file__).parents[1] / "skills/shared/scripts/ref_analysis.py"
spec = importlib.util.spec_from_file_location("ref_analysis_security", MODULE)
ref_analysis = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(ref_analysis)


def _dns(address: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/video.mp4",
    "https://user:pass@example.com/video.mp4",
    "https:///missing-host",
])
def test_remote_url_rejects_unsafe_schemes_and_credentials(url):
    with pytest.raises(ValueError):
        ref_analysis._validate_remote_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.1.2", "::1"])
def test_remote_url_rejects_non_public_addresses(monkeypatch, address):
    monkeypatch.setattr(ref_analysis.socket, "getaddrinfo", lambda *args, **kwargs: _dns(address))
    with pytest.raises(ValueError):
        ref_analysis._validate_remote_url("https://media.example/video.mp4")


def test_download_applies_size_socket_and_process_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(ref_analysis, "_validate_remote_url", lambda source: source)
    monkeypatch.setattr(ref_analysis.shutil, "which", lambda name: "/usr/local/bin/yt-dlp")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        assert list(Path(command[command.index("--ffmpeg-location") + 1]).iterdir()) == []
        target = tmp_path / "download.mp4"
        target.write_bytes(b"video")

    monkeypatch.setattr(ref_analysis, "_run", fake_run)
    assert ref_analysis._download("https://media.example/video", tmp_path).suffix == ".mp4"
    assert "--max-filesize" in captured["command"]
    assert "--socket-timeout" in captured["command"]
    assert captured["command"][captured["command"].index("--match-filters") + 1] == "!is_live"
    assert captured["kwargs"]["timeout"] == ref_analysis.DOWNLOAD_TIMEOUT_SECONDS

    YoutubeDL = pytest.importorskip("yt_dlp").YoutubeDL
    expression = captured["command"][captured["command"].index("-f") + 1]
    with YoutubeDL({"quiet": True}) as downloader:
        select = downloader.build_format_selector(expression)
        formats = [{"format_id": protocol, "url": "https://media.example/video", "ext": "mp4",
                    "height": 720, "protocol": protocol} for protocol in ("https", "rtmp")]
        selected = list(select({"formats": formats, "has_merged_format": False, "incomplete_formats": False}))
    assert [item["format_id"] for item in selected] == ["https"]


def test_real_downloader_rejects_private_hls_segment(tmp_path, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import easel.remote_media_proxy as transport

    executable = Path(__import__("sys").executable).parent / "yt-dlp"
    if not executable.is_file():
        pytest.skip("yt-dlp executable is unavailable")
    private_requests = []

    class Media(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.end_headers()

        def do_GET(self):
            if self.path == "/private.ts":
                private_requests.append(self.path)
                payload = b"private-video"
            else:
                payload = ("#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n"
                           f"http://127.0.0.1:{self.server.server_port}/private.ts\n#EXT-X-ENDLIST\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    original_validate, original_connect = transport.validate_public_url, transport.connect_public
    monkeypatch.setattr(ref_analysis, "_validate_remote_url", lambda value: value)
    monkeypatch.setattr(ref_analysis.shutil, "which", lambda _name: str(executable))
    with ThreadingHTTPServer(("127.0.0.1", 0), Media) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def validate(value):
            return value if value.startswith("http://media.example/") else original_validate(value)
        def connect(host, port, timeout):
            if host == "media.example":
                return socket.create_connection(("127.0.0.1", server.server_port), timeout)
            return original_connect(host, port, timeout)
        monkeypatch.setattr(transport, "validate_public_url", validate)
        monkeypatch.setattr(transport, "connect_public", connect)
        try:
            with pytest.raises(SystemExit) as failure:
                ref_analysis._download("http://media.example/reference.m3u8", tmp_path)
            assert failure.value.code == 4
        finally:
            server.shutdown()
            thread.join(timeout=2)
    assert private_requests == []
