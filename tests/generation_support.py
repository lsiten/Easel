"""Shared local provider and owned-project fixtures for generation contracts."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import threading

from PIL import Image
import pytest
from web import app as web


@pytest.fixture
def generation(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    Image.new('RGB', (3, 2), 'blue').save(buffer, format='PNG')
    image = buffer.getvalue()
    records = []
    observed = threading.Event()

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            records.append((self.path, self.headers.get('Authorization'), b''))
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(image)))
            self.end_headers()
            self.wfile.write(image)

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            records.append((self.path, self.headers.get('Authorization'), body))
            count = json.loads(body).get('n', 1) if 'application/json' in self.headers.get('Content-Type', '') else 1
            if b'PARTIAL_OUTPUT' in body:
                count = 1
            content = b'invalid-image' if b'INVALID_OUTPUT' in body else image
            payload = json.dumps({'created': 1730000000, 'output_format': 'png',
                'usage': {'input_tokens': 3, 'output_tokens': 5, 'total_tokens': 8},
                'private_provider_metadata': 'private-receipt-value',
                'data': [{'b64_json': base64.b64encode(content).decode(), 'revised_prompt': f'provider-refined-{index}'}
                         for index in range(count)]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            observed.set()

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    for persona in ('alice', 'bob'):
        project = outputs / (persona + '-photos')
        project.mkdir()
        (project / '.easel.json').write_text(json.dumps({'profile': persona}))
        (project / 'image.png').write_bytes(image)
    env = tmp_path / '.env'
    env.write_text('')
    monkeypatch.setattr(web, 'OUTPUTS_DIR', outputs)
    monkeypatch.setattr(web, 'ENV_FILE', env)
    monkeypatch.setattr(web, 'profile_exists', lambda name: name in {'alice', 'bob'})
    for key, value in {'IMG_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                       'IMG_API_KEY': 'private-provider-key', 'IMG_NO_PROXY': '1'}.items():
        monkeypatch.setenv(key, value)
    try:
        yield outputs, records, observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def payload(**changes):
    return {'persona': 'alice', 'request_key': 'image-one', 'model': 'provider-image', 'prompt': '春天的山', **changes}
