"""Five real provider HTTP fixtures and real media bytes for generic video contracts."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import subprocess
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from easel import _public_network
from easel.generation_video_generic_router import create_router
from easel.generation_videos import VideoGeneration


@pytest.fixture
def generic(tmp_path, monkeypatch):
    outputs = tmp_path / 'outputs'
    project = outputs / 'photos'
    project.mkdir(parents=True)
    (project / '.easel.json').write_text(json.dumps({'profile': 'alice'}))
    Image.new('RGB', (3, 2), 'blue').save(project / 'image.png')
    clip = project / 'clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:d=0.2',
                    '-c:v', 'mpeg4', str(clip)], check=True, capture_output=True)
    frame = (project / 'image.png').read_bytes()
    records = []
    behavior = {'drop': False, 'failed': False, 'identity': '', 'relay_code': 0, 'cover': False}

    class Provider(BaseHTTPRequestHandler):
        def send(self, value):
            content = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            records.append(('POST', self.path, dict(self.headers), body))
            if behavior['drop']:
                self.close_connection = True
                return
            identifier = 'remote_' + str(len(records))
            if self.path.startswith('/api/v1/'):
                self.send({'output': {'task_id': identifier, 'task_status': 'PENDING'}})
            elif self.path.startswith('/v1/videos/generations') or self.path.endswith('/edits'):
                self.send({'request_id': identifier})
            elif self.path.startswith('/api/ai/'):
                self.send({'code': behavior['relay_code'], 'data': {'id': identifier, 'status': 'submitted'}})
            else:
                self.send({'id': identifier, 'status': 'queued'})

        def do_GET(self):
            records.append(('GET', self.path, dict(self.headers), b''))
            if self.path == '/cover.png':
                self.send(frame)
                return
            if self.path == '/video.mp4' or self.path.endswith('/content'):
                self.send(clip.read_bytes())
                return
            identifier = behavior['identity'] or self.path.rsplit('/', 1)[-1]
            url = f'http://media.fixture:{self.server.server_port}/video.mp4'
            failed = behavior['failed']
            if self.path.startswith('/api/v1/'):
                self.send({'output': {'task_id': identifier, 'task_status': 'FAILED' if failed else 'SUCCEEDED', 'video_url': url}})
            elif self.path.startswith('/v1/videos/'):
                self.send({'id': identifier, 'request_id': identifier, 'status': 'failed' if failed else 'done', 'video': None if failed else {'url': url}})
            elif self.path.startswith('/api/ai/'):
                self.send({'code': behavior['relay_code'], 'data': {'id': identifier, 'status': 'failure' if failed else 'success', 'videoUrl': url, 'coverUrl': url.replace('video.mp4', 'cover.png') if behavior['cover'] else ''}})
            elif self.path.startswith('/openai/'):
                self.send({'id': identifier, 'status': 'failed' if failed else 'completed'})
            else:
                self.send({'id': identifier, 'status': 'failed' if failed else 'succeeded', 'content': {'video_url': url}})

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
    base = f'http://127.0.0.1:{server.server_port}'
    settings = {'VIDEO_API_KEY': 'key-openai', 'VIDEO_BASE_URL': base + '/openai', 'VIDEO_MODEL': 'openai-model',
        'ARK_API_KEY': 'key-volcengine', 'ARK_BASE_URL': base + '/ark', 'ARK_MODEL': 'volcengine-model',
        'EASEL_CHANNEL_MEDIA_BASE_URL': 'https://easel.example'}
    for channel in ('grok', 'dashscope', 'relay'):
        settings.update({channel.upper() + '_API_KEY': 'key-' + channel, channel.upper() + '_VIDEO_MODEL': channel + '-model',
                         channel.upper() + '_BASE_URL': base + ('/api/v1' if channel == 'dashscope' else '')})
    service = VideoGeneration(outputs, settings)
    app = FastAPI()
    app.include_router(create_router(lambda: service, lambda persona: persona in {'alice', 'bob'}, lambda request: None))
    client = TestClient(app)
    try:
        yield client, service, outputs, settings, records, behavior
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def body(channel='relay', **changes):
    return {'persona': 'alice', 'request_key': 'one', 'model': channel + '-model', 'prompt': '春天的山', **changes}
