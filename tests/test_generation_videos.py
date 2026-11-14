"""Exercise native video HTTP, durable checkpoints and the real media decoder."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import sys
import threading

from fastapi.testclient import TestClient
import pytest

from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.generation_video_models import OpenAIVideoRequest
from easel.generation_video_store import VideoStore
from easel.local_access import AccessStore
from web import app as web


@pytest.fixture
def video(tmp_path, monkeypatch):
    clip = tmp_path / 'clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:d=0.2',
                    '-c:v', 'mpeg4', str(clip)], check=True, capture_output=True)
    content = clip.read_bytes()
    records = []
    behavior = {'status': 'completed', 'invalid': False, 'drop': False, 'identity': ''}
    observed = threading.Event()

    class Provider(BaseHTTPRequestHandler):
        def respond(self, body, mime='application/json'):
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            records.append(('POST', self.path, self.headers.get('Authorization'), body))
            observed.set()
            if behavior['drop']:
                self.close_connection = True
                return
            self.respond(json.dumps({'id': 'video_' + str(len(records)), 'status': behavior['status'],
                'model': 'provider-native-model', 'progress': 35 if behavior['status'] != 'completed' else 100,
                'seconds': '4', 'created_at': 1730000000, 'private_metadata': 'private-canary',
                'prompt': 'actual provider prompt', 'remixed_from_video_id': 'video_original' if self.path.endswith('/remix') else None,
                'error': {'code': 'content_policy', 'message': 'blocked private-provider-key'} if behavior['status'] == 'failed' else None}).encode())

        def do_GET(self):
            records.append(('GET', self.path, self.headers.get('Authorization'), b''))
            if self.path.endswith('/content'):
                self.respond(b'not-a-video' if behavior['invalid'] else content, 'video/mp4')
            else:
                self.respond(json.dumps({'id': behavior['identity'] or self.path.rsplit('/', 1)[-1],
                                         'status': behavior['status'], 'progress': 100}).encode())

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    monkeypatch.setattr(web, 'OUTPUTS_DIR', outputs)
    env = tmp_path / '.env'
    env.write_text('')
    monkeypatch.setattr(web, 'ENV_FILE', env)
    monkeypatch.setattr(web, 'profile_exists', lambda name: name in {'alice', 'bob'})
    monkeypatch.setenv('VIDEO_BASE_URL', f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('VIDEO_API_KEY', 'private-provider-key')
    monkeypatch.setenv('VIDEO_MODEL', 'configured-video-model')
    client = TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000))
    try:
        yield client, outputs, records, behavior, observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(**changes):
    return {'persona': 'alice', 'request_key': 'video-one', 'prompt': '春天的山', 'seconds': '4', 'size': '1280x720', **changes}


def test_native_video_archives_real_bytes_and_remix_uses_owned_native_identity(video, monkeypatch):
    client, outputs, records, _, _ = video
    result = client.post('/api/ai/openai/videos', json=request())
    assert result.status_code == 200, result.text
    job = result.json()
    assert job['state'] == 'succeeded' and job['asset']['kind'] == 'video'
    assert 'private-provider-key' not in result.text and 'private-canary' not in result.text
    assert (outputs / job['asset']['path']).read_bytes()[4:8] == b'ftyp'
    assert client.get('/api/ai/assets?persona=alice').json()['items'][0]['sha256'] == job['asset']['sha256']
    assert client.get('/api/ai/assets?persona=bob').json()['total'] == 0
    assert len(records) == 2 and records[1][1] == '/v1/videos/' + job['provider_id'] + '/content'
    assert all(row[2] == 'Bearer private-provider-key' for row in records)
    assert b'configured-video-model' in records[0][3] and b'name="seconds"' in records[0][3]
    remix = client.post('/api/ai/openai/videos/' + job['id'] + '/remix', json={
        'persona': 'alice', 'request_key': 'remix-one', 'prompt': '下雨的山'})
    assert remix.json()['state'] == 'succeeded' and remix.json()['id'] != job['id']
    assert remix.json()['response']['remixed_from_video_id'] == 'video_original'
    assert job['response']['prompt'] == 'actual provider prompt'
    assert records[2][1] == '/v1/videos/' + job['provider_id'] + '/remix'
    assert json.loads(records[2][3]) == {'prompt': '下雨的山'}
    assert client.get('/api/ai/openai/videos?persona=alice&page_size=1').json()['total'] == 2
    monkeypatch.delenv('VIDEO_API_KEY')
    assert client.post('/api/ai/openai/videos', json=request()).json()['id'] == job['id']
    assert len(records) == 4


def test_reference_ownership_is_enforced_before_native_submission(video):
    from PIL import Image
    client, outputs, records, _, _ = video
    directory = outputs / 'photos'
    directory.mkdir()
    (directory / '.easel.json').write_text(json.dumps({'profile': 'alice'}))
    Image.new('RGB', (3, 2), 'blue').save(directory / 'ref.png')
    good = client.post('/api/ai/openai/videos', json=request(input_reference='/api/media/photos/ref.png'))
    assert good.status_code == 200 and good.json()['state'] == 'succeeded'
    assert b'name="input_reference"' in records[0][3] and b'PNG' in records[0][3] and b'image/png' in records[0][3]
    denied = client.post('/api/ai/openai/videos', json=request(persona='bob', input_reference='photos/ref.png'))
    assert denied.status_code == 403 and len(records) == 2


def test_interrupted_submission_is_unknown_and_replay_never_bills_again(video):
    client, _, records, behavior, _ = video
    behavior['drop'] = True
    first = client.post('/api/ai/openai/videos', json=request())
    assert first.json()['state'] == 'unknown'
    assert client.post('/api/ai/openai/videos', json=request()).json()['id'] == first.json()['id']
    assert len(records) == 1
    assert client.post('/api/ai/openai/videos', json=request(prompt='changed')).status_code == 409


def test_pending_native_video_resumes_by_get_without_second_submission(video):
    client, outputs, records, behavior, _ = video
    behavior['status'] = 'in_progress'
    first = client.post('/api/ai/openai/videos', json=request()).json()
    assert first['state'] == 'waiting' and first['response']['progress'] == 35
    assert VideoStore(GenerationStore(outputs)).get('alice', first['id']).provider_id == first['provider_id']
    behavior['status'] = 'completed'
    result = client.get('/api/ai/openai/videos/' + first['id'] + '?persona=alice').json()
    assert result['state'] == 'succeeded' and result['asset']['kind'] == 'video'
    assert [row[0] for row in records] == ['POST', 'GET', 'GET']
    assert records[1][1] == '/v1/videos/' + first['provider_id']


def test_paid_native_video_waits_for_restored_credentials_and_resumes_by_get(video, monkeypatch):
    client, _, records, behavior, _ = video
    behavior['status'] = 'in_progress'
    first = client.post('/api/ai/openai/videos', json=request()).json()
    endpoint = '/api/ai/openai/videos/' + first['id'] + '?persona=alice'
    monkeypatch.delenv('VIDEO_API_KEY')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('API_KEY', raising=False)
    paused = client.get(endpoint).json()
    assert paused['state'] == 'waiting' and paused['error'] == 'video_configuration_required'
    assert paused['provider_id'] == first['provider_id'] and len(records) == 1
    monkeypatch.setenv('VIDEO_API_KEY', 'private-provider-key')
    behavior['status'] = 'completed'
    completed = client.get(endpoint).json()
    assert completed['state'] == 'succeeded'
    assert [row[0] for row in records] == ['POST', 'GET', 'GET']


def test_provider_identity_mismatch_prevents_foreign_video_download(video):
    client, _, records, behavior, _ = video
    behavior['status'] = 'queued'
    first = client.post('/api/ai/openai/videos', json=request()).json()
    behavior.update(status='completed', identity='video_someone_else')
    result = client.get('/api/ai/openai/videos/' + first['id'] + '?persona=alice').json()
    assert result['state'] == 'failed' and result['error'] == 'video_provider_identity_mismatch'
    assert len(records) == 2


def test_invalid_completed_artifact_is_failed_without_library_entry(video):
    client, outputs, records, behavior, _ = video
    behavior['invalid'] = True
    result = client.post('/api/ai/openai/videos', json=request()).json()
    assert result['state'] == 'failed' and result['error'] == 'video_result_invalid'
    assert client.get('/api/ai/assets?persona=alice').json()['total'] == 0
    assert not (outputs / ('AI视频-' + result['id']) / '.easel.json').exists()
    assert len(records) == 2


def test_provider_decline_preserves_error_metadata_and_redacts_configured_secret(video):
    client, _, records, behavior, _ = video
    behavior['status'] = 'failed'
    result = client.post('/api/ai/openai/videos', json=request())
    assert result.json()['state'] == 'failed'
    assert result.json()['response']['error'] == {'code': 'content_policy', 'message': 'blocked [redacted]'}
    assert 'private-provider-key' not in result.text and len(records) == 1


def test_video_queries_and_mutations_obey_persona_and_key_permissions(video):
    client, outputs, records, _, _ = video
    job = client.post('/api/ai/openai/videos', json=request()).json()
    key = AccessStore(outputs / '_access').create_key('alice', 'read video', 'read')['key']
    headers = {'x-easel-api-key': key}
    endpoint = '/api/ai/openai/videos/' + job['id']
    assert client.get(endpoint + '?persona=alice', headers=headers).status_code == 200
    assert client.get(endpoint + '?persona=bob', headers=headers).status_code == 403
    assert client.get(endpoint + '?persona=bob').status_code == 404
    assert client.post('/api/ai/openai/videos', json=request(), headers=headers).status_code == 403
    assert client.post(endpoint + '/remix', json={'persona': 'bob', 'request_key': 'foreign', 'prompt': 'rain'}).status_code == 404
    assert len(records) == 2


def test_live_claims_are_fenced_and_dead_workers_resume_only_known_provider_tasks(video):
    _, outputs, _, _, _ = video
    service = web._video_generation()
    first = service.submit(OpenAIVideoRequest(**request()))
    second = service.submit(OpenAIVideoRequest(**request(request_key='second')))
    third = service.submit(OpenAIVideoRequest(**request(request_key='third')))
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda owner: service.store.claim(owner, first.id), ['owner-a', 'owner-b']))
    assert sum(job is not None for job in claims) == 1
    claimed = next(job for job in claims if job is not None)
    with pytest.raises(GenerationError, match='generation_worker_stale'):
        service.store.save(claimed, 'foreign-owner', 'succeeded')
    script = '''from easel.generation_store import GenerationStore
from easel.generation_video_store import VideoStore
from easel.publish_recovery import worker_owner
from pathlib import Path
import os, sys
store=VideoStore(GenerationStore(Path(sys.argv[1])))
owner=worker_owner()
job=store.claim(owner, sys.argv[2])
if sys.argv[3] == 'known':
    store.save(job.model_copy(update={'provider_id': 'video_existing'}), owner, 'submitting')
os._exit(0)
'''
    subprocess.run([sys.executable, '-c', script, str(outputs), second.id, 'known'], check=True)
    subprocess.run([sys.executable, '-c', script, str(outputs), third.id, 'unknown'], check=True)
    assert VideoStore(GenerationStore(outputs)).recover() == 2
    assert service.store.get('alice', first.id).state == 'submitting'
    assert service.store.get('alice', second.id).state == 'waiting'
    assert service.store.get('alice', third.id).state == 'unknown'
