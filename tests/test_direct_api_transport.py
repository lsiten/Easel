"""Exercise the current direct-API transport against a real loopback server."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import anyio
import pytest

from easel import direct_api


def test_local_chat_bypasses_inherited_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            body = (
                'data: {"choices":[{"delta":{"content":"local reply"},"finish_reason":"stop"}]}\n\n'
                'data: [DONE]\n\n'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: str) -> None:
            """Keep the local fixture server quiet."""

    for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:9")
    for key in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(key, raising=False)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        settings = {
            "EASEL_DIRECT_API_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
            "EASEL_DIRECT_API_MODEL": "local-test-model",
            "EASEL_DIRECT_API_KEY": "",
        }

        async def reply() -> list[tuple[str, str]]:
            return [event async for event in direct_api.stream_chat(settings, "hello", timeout=2)]

        try:
            events = anyio.run(reply)
        finally:
            server.shutdown()
            worker.join(timeout=5)

    assert ("token", "local reply") in events
    assert requests == ["/v1/chat/completions"]
