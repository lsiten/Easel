"""Pinned multipart character extension via real HTTP and owned video receipts."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from fastapi.testclient import TestClient
import pytest

from web import app as web


@pytest.fixture
def characters(tmp_path, monkeypatch):
    records = []
    behavior = {'drop': False, 'foreign_id': False, 'failed': False}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def send(self, value):
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            records.append(('POST', self.path, body))
            if b'sora-2-character' not in body:
                self.send({'id': 'native_video', 'status': 'queued', 'model': 'sora-2'})
            elif behavior['drop']:
                self.close_connection = True
            else:
                self.send({'id': 'native_character', 'object': 'character', 'status': 'queued',
                           'username': 'actor', 'created_at': 1730000000})

        def do_GET(self):
            records.append(('GET', self.path, b''))
            self.send({'id': 'unrelated_character' if behavior['foreign_id'] else 'native_character',
                'object': 'character', 'status': 'failed' if behavior['failed'] else 'completed',
                'username': 'actor', 'avatar_url': 'https://cdn.example/actor.png',
                'error': {'code': 42, 'message': 'test-character-private-key rejected'} if behavior['failed'] else None})

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    env = tmp_path / '.env'
    env.write_text('')
    monkeypatch.setattr(web, 'OUTPUTS_DIR', outputs)
    monkeypatch.setattr(web, 'ENV_FILE', env)
    monkeypatch.setattr(web, 'profile_exists', lambda name: name in {'alice', 'bob'})
    monkeypatch.setenv('VIDEO_BASE_URL', f'http://127.0.0.1:{provider.server_port}/v1')
    monkeypatch.setenv('VIDEO_API_KEY', 'test-character-private-key')
    monkeypatch.setenv('VIDEO_MODEL', 'sora-2')
    try:
        with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
            source = client.post('/api/ai/openai/videos', json={'persona': 'alice', 'request_key': 'source', 'prompt': 'actor'})
            assert source.status_code == 200 and source.json()['provider_id'] == 'native_video'
            yield client, source.json()['id'], records, behavior
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=3)


def payload(source, **updates):
    return {'persona': 'alice', 'request_key': 'actor', 'prompt': 'main actor', 'taskId': source,
            'timestamps': '1,3', **updates}


def test_character_native_id_restart_history_and_offline_replay(characters, monkeypatch):
    client, source, records, _ = characters
    request = payload(source)
    result = client.post('/api/ai/openai/characters', json=request)
    assert result.status_code == 200 and result.json()['state'] == 'running'
    job = result.json()
    assert b'name="taskId"\r\n\r\nnative_video' in records[-1][2]
    assert b'name="timestamps"\r\n\r\n1,3' in records[-1][2]
    detail = client.get('/api/ai/openai/characters/' + job['id'] + '?persona=alice')
    assert detail.status_code == 200 and detail.json()['state'] == 'succeeded'
    assert detail.json()['response']['username'] == 'actor' and records[-1][1] == '/v1/videos/native_character'
    count = len(records)
    monkeypatch.delenv('VIDEO_API_KEY')
    replay = client.post('/api/ai/openai/characters', json=request)
    assert replay.status_code == 200 and replay.json()['id'] == job['id'] and len(records) == count
    history = client.get('/api/ai/logs?persona=alice&type=character')
    assert history.status_code == 200 and history.json()['total'] == 1
    assert history.json()['items'][0]['state'] == 'succeeded'
    assert client.post('/api/ai/openai/characters', json=payload(source, prompt='different')).status_code == 409


def test_character_owner_validation_and_source_identity_gate(characters):
    client, source, records, behavior = characters
    count = len(records)
    assert client.post('/api/ai/openai/characters', json=payload(source, persona='bob')).status_code == 404
    assert client.post('/api/ai/openai/characters', json=payload(source, timestamps='1,8')).status_code == 422
    assert len(records) == count
    result = client.post('/api/ai/openai/characters', json=payload(source)).json()
    assert client.get('/api/ai/openai/characters/' + result['id'] + '?persona=bob').status_code == 404
    behavior['foreign_id'] = True
    detail = client.get('/api/ai/openai/characters/' + result['id'] + '?persona=alice').json()
    assert detail['state'] == 'running' and detail['error'] == 'character_reconciliation_pending'
    behavior.update(foreign_id=False, failed=True)
    failed = client.get('/api/ai/openai/characters/' + result['id'] + '?persona=alice')
    assert failed.json()['state'] == 'failed' and 'test-character-private-key' not in failed.text


def test_character_lost_paid_receipt_and_concurrent_replay_are_not_resubmitted(characters):
    client, source, records, behavior = characters
    behavior['drop'] = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post('/api/ai/openai/characters', json=payload(source)), range(2)))
    assert all(response.status_code == 200 for response in results)
    assert len({response.json()['id'] for response in results}) == 1
    assert sum(b'sora-2-character' in record[2] for record in records) == 1
    identifier = results[0].json()['id']
    assert client.get('/api/ai/openai/characters/' + identifier + '?persona=alice').json()['state'] == 'unknown'
