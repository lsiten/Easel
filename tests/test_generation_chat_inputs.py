"""Owned chat media and replay through real HTTP provider and application routes."""
import base64
import json
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from easel.generation_chat_router import create_router
from easel.generation_chat_service import ChatGeneration
from easel.generation_store import GenerationStore
from easel.generation_video_generic_router import create_router as video_router
from easel.generation_videos import VideoGeneration


@pytest.fixture
def media_surface(tmp_path):
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if body['stream'] else 'application/json')
            self.end_headers()
            if not body['stream']:
                self.wfile.write(b'{"choices":[{"message":{"content":"answer"}}],"usage":{}}')
            elif self.path.endswith('/messages'):
                self.wfile.write(b'data: {"type":"message_stop"}\n\n')
            else:
                self.wfile.write(b'data: {"choices":[]}\n\ndata: [DONE]\n\n')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = {'EASEL_DIRECT_API_MODEL': 'test', 'ANTHROPIC_MODEL': 'test',
                'EASEL_DIRECT_API_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1'}
    for name, owner in [('owned', 'alice'), ('foreign', 'bob')]:
        project = tmp_path / name
        project.mkdir()
        (project / '.easel.json').write_text(json.dumps({'profile': owner}))
        Image.new('RGB', (2, 2), 'red').save(project / 'photo.png')
    store = GenerationStore(tmp_path)
    app = FastAPI()
    app.include_router(create_router(lambda: ChatGeneration(store, settings), lambda name: True, lambda request: None))
    app.include_router(video_router(lambda: VideoGeneration(tmp_path, settings), lambda name: True, lambda request: None))
    with TestClient(app) as client:
        yield client, store, settings, calls, tmp_path
    server.shutdown()
    server.server_close()
    thread.join()


def payload(content, key='media'):
    return {'persona': 'alice', 'request_key': key, 'model': 'test',
            'messages': [{'role': 'user', 'content': content}]}


@pytest.mark.parametrize('reference', ['owned/photo.png', '/api/media/owned/photo.png',
                                     'http://testserver/api/media/owned/photo.png'])
def test_local_images_resolve_inside_text_and_nested_messages(media_surface, reference):
    client, _, _, calls, root = media_surface
    content = [{'type': 'text', 'text': f'Inspect ({reference})'},
               {'type': 'image_url', 'image_url': {'url': reference, 'detail': 'high'}}]
    response = client.post('/api/ai/chat', json={**payload(content), 'tools': [{'type': 'function', 'name': 'lookup'}]})
    assert response.status_code == 200
    sent = calls[0]['messages'][0]['content']
    uri = sent[1]['image_url']['url']
    assert uri.startswith('data:image/png;base64,')
    assert base64.b64decode(uri.split(',', 1)[1]) == (root / 'owned/photo.png').read_bytes()
    assert sent[0]['text'] == f'Inspect ({uri})' and sent[1]['image_url']['detail'] == 'high'
    assert calls[0]['tools'] == [{'type': 'function', 'name': 'lookup'}]


@pytest.mark.parametrize('reference', ['/api/media/foreign/photo.png', 'foreign/photo.png',
                                     '/api/media/%2e%2e/private.png'])
def test_foreign_or_escaping_media_rejected_before_provider_and_claim(media_surface, reference):
    client, store, _, calls, _ = media_surface
    response = client.post('/api/ai/chat', json=payload(reference))
    assert response.status_code == 403 and calls == []
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM chat_jobs').fetchone()[0] == 0


@pytest.mark.parametrize('route', ['', '/stream', '/claude'])
def test_completed_replay_survives_deleted_original_and_removed_configuration(media_surface, route):
    client, _, settings, calls, root = media_surface
    request = payload('/api/media/owned/photo.png')
    first = client.post('/api/ai/chat' + route, json=request)
    assert first.status_code == 200
    (root / 'owned/photo.png').unlink()
    settings.clear()
    replay = client.post('/api/ai/chat' + route, json=request)
    assert replay.status_code == 200 and replay.content == first.content and len(calls) == 1
    assert client.post('/api/ai/chat' + route, json=payload('changed')).status_code == 409
    other_route = '/stream' if route != '/stream' else ''
    assert client.post('/api/ai/chat' + other_route, json=request).status_code == 409


def test_external_urls_native_tools_and_plain_text_remain_lossless(media_surface):
    client, _, _, calls, _ = media_surface
    content = [{'type': 'text', 'text': 'ordinary text and/or punctuation'},
               {'type': 'image_url', 'image_url': {'url': 'https://external.invalid/api/media/owned/photo.png'}}]
    tools = [{'type': 'function', 'function': {'name': 'lookup', 'parameters': {'type': 'object'}}}]
    response = client.post('/api/ai/chat/stream', json={**payload(content), 'tools': tools, 'tool_choice': 'auto'})
    assert response.status_code == 200 and '[DONE]' in response.text
    assert calls[0]['messages'][0]['content'] == content and calls[0]['tools'] == tools


@pytest.mark.parametrize('configured', [False, True])
def test_local_audio_requires_public_base_and_uses_frozen_expiring_grant(media_surface, configured):
    client, store, settings, calls, root = media_surface
    path = root / 'owned/audio.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0\0' * 800)
    original = path.read_bytes()
    if configured:
        settings['EASEL_CHANNEL_MEDIA_BASE_URL'] = 'https://public.example'
    response = client.post('/api/ai/chat', json=payload('/api/media/owned/audio.wav'))
    if not configured:
        assert response.status_code == 409 and calls == []
        return
    assert response.status_code == 200 and len(calls) == 1
    url = calls[0]['messages'][0]['content']
    assert url.startswith('https://public.example/api/ai/video/media/')
    path.unlink()
    endpoint = url.removeprefix('https://public.example')
    assert client.get(endpoint).content == original
    with store.connect() as db:
        db.execute("UPDATE chat_jobs SET persona='bob'")
    assert client.get(endpoint).status_code == 403
    with store.connect() as db:
        db.execute("UPDATE chat_jobs SET persona='alice',state='unknown'")
    assert client.get(endpoint).status_code == 403
    with store.connect() as db:
        db.execute("UPDATE chat_jobs SET state='succeeded'")
    frozen = next((root / '_publish/alice').glob('*/media/*.wav'))
    frozen.chmod(0o600)
    frozen.write_bytes(b'changed')
    assert client.get(endpoint).status_code == 409
    with store.connect() as db:
        db.execute('UPDATE video_media_grants SET expires_at=0')
    assert client.get(endpoint).status_code == 403


@pytest.mark.parametrize('reference', ['/api/media/owned/photo.png', '/api/media/owned/%00photo.png'])
def test_invalid_local_image_rejected_before_paid_post(media_surface, reference):
    client, store, _, calls, root = media_surface
    (root / 'owned/photo.png').write_bytes(b'not an image')
    response = client.post('/api/ai/chat', json=payload(reference))
    assert response.status_code == 400 and calls == []
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM chat_jobs').fetchone()[0] == 0
