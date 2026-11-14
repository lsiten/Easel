"""Actual Ark and public media HTTP transports for native protocol contracts."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import subprocess
import threading

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from easel import _public_network
from web import app as web


@pytest.fixture
def ark(tmp_path, monkeypatch):
    clip = tmp_path / 'clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:d=0.2',
                    '-c:v', 'mpeg4', str(clip)], check=True, capture_output=True)
    media = clip.read_bytes()
    frame = tmp_path / 'last.png'
    Image.new('RGB', (3, 2), 'red').save(frame)
    frame_bytes = frame.read_bytes()
    records = []
    behavior = {'status': 'succeeded', 'identity': '', 'drop': False, 'frame_failure': ''}

    class Provider(BaseHTTPRequestHandler):
        def send(self, content, mime='application/json'):
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            records.append(('POST', self.path, self.headers.get('Authorization'), json.loads(body)))
            if behavior['drop']:
                self.close_connection = True
                return
            self.send(json.dumps({'id': 'task_' + str(len(records))}).encode())

        def do_GET(self):
            records.append(('GET', self.path, self.headers.get('Authorization'), None))
            if self.path == '/video.mp4':
                self.send(media, 'video/mp4')
                return
            if self.path == '/last.png':
                if behavior['frame_failure'] == 'unavailable':
                    self.send_error(503)
                    return
                self.send(b'not-an-image' if behavior['frame_failure'] == 'invalid' else frame_bytes, 'image/png')
                return
            self.send(json.dumps({'id': behavior['identity'] or self.path.rsplit('/', 1)[-1],
                'status': behavior['status'], 'model': 'actual-model', 'created_at': 1730000000,
                'updated_at': 1730000005, 'seed': 17, 'resolution': '720p', 'ratio': '16:9',
                'duration': 5, 'framespersecond': 24, 'private': 'private-canary',
                'usage': {'completion_tokens': 10, 'total_tokens': 12, 'tool_usage': {'web_search': 1}},
                'content': {'video_url': f'http://media.fixture:{self.server.server_port}/video.mp4', 'last_frame_url': f'http://media.fixture:{self.server.server_port}/last.png', 'debug': 'private-canary'},
                'error': {'code': behavior['status'], 'message': 'provider private-ark-key', 'debug': 'private-canary'} if behavior['status'] in {'failed', 'expired', 'cancelled'} else None}).encode())

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = _public_network.resolve_public
    def resolve(host, port):
        if host == 'media.fixture':
            return (_public_network.PublicAddress(socket.AF_INET, socket.SOCK_STREAM, 6, ('127.0.0.1', server.server_port)),)
        return original(host, port)
    monkeypatch.setattr(_public_network, 'resolve_public', resolve)
    outputs = tmp_path / 'outputs'
    project = outputs / 'alice-photos'
    project.mkdir(parents=True)
    (project / '.easel.json').write_text(json.dumps({'profile': 'alice'}))
    Image.new('RGB', (3, 2), 'blue').save(project / 'image.png')
    env = tmp_path / '.env'
    env.write_text('')
    monkeypatch.setattr(web, 'OUTPUTS_DIR', outputs)
    monkeypatch.setattr(web, 'ENV_FILE', env)
    monkeypatch.setattr(web, 'profile_exists', lambda name: name in {'alice', 'bob'})
    for key, value in {'ARK_BASE_URL': f'http://127.0.0.1:{server.server_port}/api/v3',
                       'ARK_API_KEY': 'private-ark-key', 'ARK_MODEL': 'configured-ark',
                       'VIDEO_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                       'VIDEO_API_KEY': 'openai-test-key', 'VIDEO_MODEL': 'configured-openai'}.items():
        monkeypatch.setenv(key, value)
    client = TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000))
    try:
        yield client, outputs, records, behavior
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(**changes):
    return {'persona': 'alice', 'request_key': 'ark-one', 'model': 'configured-ark',
            'content': [{'type': 'text', 'text': '春天的山'}], **changes}
