"""Native Ark entry points, persisted receipts, and unified generation logs."""
import base64
import json

import pytest
from PIL import Image

from easel.generation_models import ImageRequest
from easel.generation_store import GenerationStore
from easel.generation_video_store import VideoStore
from easel.generation_video_models import OpenAIVideoRequest
from easel.local_access import AccessStore
from web import app as web
from tests.volcengine_support import ark as ark, request


def test_native_ark_normalizes_parameters_and_archives_owned_receipt(ark):
    client, outputs, records, _ = ark
    created = client.post('/api/ai/volcengine/video', json=request(content=[
        {'type': 'text', 'text': '春天的山 --rs 480p --rt 9:16 --dur 3 --dur 5 --seed 17 --wm true'}],
        resolution='720p', return_last_frame=True, tools=[{'type': 'web_search'}]))
    assert created.status_code == 200 and created.json()['state'] == 'waiting', created.text
    job = created.json()
    wire = records[0][3]
    assert records[0][:3] == ('POST', '/api/v3/contents/generations/tasks', 'Bearer private-ark-key')
    assert wire['content'] == [{'type': 'text', 'text': '春天的山'}]
    assert (wire['resolution'], wire['ratio'], wire['duration'], wire['seed'], wire['watermark']) == ('720p', '9:16', 5, 17, True)
    assert wire['return_last_frame'] is True and wire['tools'] == [{'type': 'web_search'}]
    result = client.get('/api/ai/volcengine/video/' + job['id'] + '?persona=alice')
    assert result.json()['state'] == 'succeeded' and result.json()['asset']['kind'] == 'video'
    receipt = result.json()['response']['volcengine']
    assert receipt['status'] == 'succeeded' and receipt['usage']['total_tokens'] == 12
    assert receipt['usage']['tool_usage'] == {'web_search': 1} and receipt['framespersecond'] == 24
    assert 'private-ark-key' not in result.text and 'private-canary' not in result.text
    assert (outputs / result.json()['asset']['path']).read_bytes()[4:8] == b'ftyp'
    assert [(row[1], row[2]) for row in records[2:]] == [('/last.png', None), ('/video.mp4', None)]
    assert receipt['content']['video_url'].startswith('/api/media/') and receipt['content']['last_frame_url'].startswith('/api/media/')
    frame = result.json()['additional_assets'][0]
    with Image.open(outputs / frame['path']) as picture:
        assert picture.size == (3, 2) and frame['kind'] == 'image'
    assets = client.get('/api/ai/assets?persona=alice').json()
    assert assets['total'] == 2 and {item['kind'] for item in assets['items']} == {'image', 'video'}
    assert client.get('/api/ai/openai/videos/' + job['id'] + '?persona=alice').status_code == 404
    assert client.post('/api/ai/openai/videos/' + job['id'] + '/remix', json={'persona': 'alice', 'request_key': 'remix', 'prompt': 'rain'}).status_code == 404


def test_native_ark_freezes_owned_first_and_last_frame_bytes(ark, monkeypatch):
    client, outputs, records, _ = ark
    image = '/api/media/alice-photos/image.png'
    body = request(content=[{'type': 'text', 'text': 'start and end'},
        {'type': 'image_url', 'image_url': {'url': image}, 'role': 'first_frame'},
        {'type': 'image_url', 'image_url': {'url': image}, 'role': 'last_frame'}])
    result = client.post('/api/ai/volcengine/video', json=body).json()
    assert result['state'] == 'waiting' and len(result['inputs']) == 1
    sent = records[0][3]['content']
    assert [item.get('role') for item in sent] == [None, 'first_frame', 'last_frame']
    assert base64.b64decode(sent[1]['image_url']['url'].partition(',')[2]) == (outputs / 'alice-photos/image.png').read_bytes()
    assert sent[1]['image_url'] == sent[2]['image_url']
    denied = client.post('/api/ai/volcengine/video', json={**body, 'persona': 'bob'})
    assert denied.status_code == 403 and len(records) == 1
    assert client.get('/api/ai/volcengine/video/' + result['id'] + '?persona=alice').json()['state'] == 'succeeded'
    (outputs / 'alice-photos/image.png').unlink()
    monkeypatch.delenv('ARK_API_KEY')
    replay = client.post('/api/ai/volcengine/video', json=body)
    assert replay.status_code == 200 and replay.json()['id'] == result['id'] and len(records) == 4


@pytest.mark.parametrize('content', [
    [{'type': 'image_url', 'image_url': {'url': 'https://example.com/last.png'}, 'role': 'last_frame'}],
    [{'type': 'image_url', 'image_url': {'url': 'https://example.com/first.png'}},
     {'type': 'image_url', 'image_url': {'url': 'https://example.com/ref.png'}, 'role': 'reference_image'}],
    [{'type': 'audio_url', 'audio_url': {'url': 'https://example.com/audio.wav'}, 'role': 'reference_audio'}],
])
def test_invalid_native_reference_scene_is_rejected_before_submission(ark, content):
    client, outputs, records, _ = ark
    result = client.post('/api/ai/volcengine/video', json=request(content=[{'type': 'text', 'text': 'prompt'}, *content]))
    assert result.status_code == 422 and not records
    assert web._video_generation().store.list('alice').total == 0


@pytest.mark.parametrize('status', ['cancelled', 'expired', 'failed'])
def test_native_provider_terminal_failures_preserve_reason_without_key_or_assets(ark, status):
    client, _, records, behavior = ark
    created = client.post('/api/ai/volcengine/video', json=request()).json()
    behavior['status'] = status
    response = client.get('/api/ai/volcengine/video/' + created['id'] + '?persona=alice')
    assert response.json()['state'] == 'failed'
    error = response.json()['response']['error']
    assert error == {'code': status, 'message': 'provider [redacted]'}
    assert response.json()['response']['volcengine']['status'] == status
    assert 'private-ark-key' not in response.text and len(records) == 2
    assert client.get('/api/ai/assets?persona=alice').json()['total'] == 0


def test_native_ark_waits_for_credentials_then_queries_without_resubmission(ark, monkeypatch):
    client, _, records, _ = ark
    job = client.post('/api/ai/volcengine/video', json=request()).json()
    endpoint = '/api/ai/volcengine/video/' + job['id'] + '?persona=alice'
    monkeypatch.delenv('ARK_API_KEY')
    monkeypatch.delenv('VOLCENGINE_API_KEY', raising=False)
    monkeypatch.delenv('VOLC_ARK_API_KEY', raising=False)
    missing = client.get(endpoint).json()
    assert missing['state'] == 'waiting' and missing['provider_id'] == job['provider_id'] and len(records) == 1
    monkeypatch.setenv('ARK_API_KEY', 'private-ark-key')
    assert client.get(endpoint).json()['state'] == 'succeeded'
    assert [row[0] for row in records] == ['POST', 'GET', 'GET', 'GET']


def test_native_ark_response_loss_is_unknown_and_private_input_is_rejected(ark):
    client, _, records, behavior = ark
    behavior['drop'] = True
    first = client.post('/api/ai/volcengine/video', json=request()).json()
    assert first['state'] == 'unknown'
    assert client.post('/api/ai/volcengine/video', json=request()).json()['id'] == first['id'] and len(records) == 1
    blocked = client.post('/api/ai/volcengine/video', json=request(request_key='private-video', content=[
        {'type': 'text', 'text': 'prompt'}, {'type': 'video_url', 'video_url': {'url': 'http://127.0.0.1/video'}, 'role': 'reference_video'}])).json()
    assert blocked['state'] == 'failed' and len(records) == 1


def test_native_ark_model_alias_and_unified_logs_keep_persona_and_channel(ark, monkeypatch):
    client, outputs, records, _ = ark
    monkeypatch.delenv('ARK_API_KEY')
    monkeypatch.setenv('VOLCENGINE_API_KEY', 'private-ark-key')
    preference = {'default_model': 'friendly-ark', 'options': {'channel': 'volcengine',
        'runtimeModel': 'actual-runtime', 'modes': ['reference2video'], 'durations': [5], 'private': 'catalog-canary'}}
    assert client.put('/api/local-user/ai/video?persona=alice', json=preference).status_code == 200
    catalog = client.get('/api/ai/models/video/generation?persona=alice')
    assert 'catalog-canary' not in catalog.text and 'private-ark-key' not in catalog.text
    assert next(row for row in catalog.json() if row['name'] == 'friendly-ark')['channel'] == 'volcengine'
    job = client.post('/api/ai/volcengine/video', json=request(model='friendly-ark')).json()
    assert records[0][3]['model'] == 'actual-runtime'
    GenerationStore(outputs).enqueue(ImageRequest(persona='alice', request_key='image', prompt='image', model='image-model'))
    web._video_generation().submit(OpenAIVideoRequest(persona='alice', request_key='openai', prompt='video'))
    all_logs = client.get('/api/ai/logs?persona=alice&page_size=1').json()
    assert all_logs['total'] == 3 and len(all_logs['items']) == 1 and 'request' not in all_logs['items'][0]
    filtered = client.get('/api/ai/logs?persona=alice&type=video&channel=volcengine&model=friendly-ark&status=generating').json()
    assert filtered['total'] == 1 and filtered['items'][0]['id'] == job['id'] and filtered['items'][0]['state'] == 'waiting'
    assert client.get('/api/ai/logs?persona=bob').json()['total'] == 0
    key = AccessStore(outputs / '_access').create_key('alice', 'reader', 'read')['key']
    headers = {'x-easel-api-key': key}
    assert client.get('/api/ai/models/video/generation?persona=alice', headers=headers).status_code == 200
    assert client.post('/api/ai/volcengine/video', headers=headers, json=request(request_key='forbidden')).status_code == 403
    assert len(records) == 1


def test_legacy_native_video_rows_upgrade_without_resetting_identity_or_requested_model(ark):
    _, outputs, records, _ = ark
    generation = GenerationStore(outputs)
    legacy = {'id': 'c' * 32, 'persona': 'alice', 'state': 'failed', 'base_url': 'https://example.com/v1',
              'runtime_model': 'old-runtime', 'created_at': 100, 'updated_at': 200,
              'request': {'persona': 'alice', 'request_key': 'legacy', 'prompt': 'legacy prompt', 'model': 'old-alias'}}
    with generation.connect() as db:
        db.execute('''CREATE TABLE video_jobs(id TEXT PRIMARY KEY,persona TEXT,request_key TEXT,digest TEXT,
            state TEXT,owner TEXT,model TEXT,operation TEXT,created_at REAL,updated_at REAL,ready_at REAL,body TEXT)''')
        db.execute('INSERT INTO video_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (legacy['id'], 'alice', 'legacy', 'digest', 'failed', '', 'old-runtime', 'create', 100, 200, 0, json.dumps(legacy)))
    assert generation.list('alice', kind='video', channel='openai').total == 1
    store = VideoStore(generation)
    job = store.get('alice', legacy['id'], 'openai')
    assert job.request.model == 'old-alias' and job.runtime_model == 'old-runtime' and job.updated_at == 200
    page = generation.list('alice', model='old-alias', channel='openai', status='failed')
    assert page.total == 1 and page.items[0].id == legacy['id'] and not records


def test_invalid_last_frame_keeps_outputs_out_of_completed_asset_index(ark):
    client, outputs, records, behavior = ark
    first = client.post('/api/ai/volcengine/video', json=request()).json()
    behavior['frame_failure'] = 'invalid'
    result = client.get('/api/ai/volcengine/video/' + first['id'] + '?persona=alice').json()
    assert result['state'] == 'failed' and result['additional_assets'] == []
    assert client.get('/api/ai/assets?persona=alice').json()['total'] == 0
    assert not (outputs / ('AI视频-' + first['id']) / '.easel.json').exists()
    assert [row[1] for row in records][-1] == '/last.png' and len(records) == 3


def test_last_frame_download_recovers_without_creating_another_paid_task(ark):
    client, _, records, behavior = ark
    first = client.post('/api/ai/volcengine/video', json=request()).json()
    endpoint = '/api/ai/volcengine/video/' + first['id'] + '?persona=alice'
    behavior['frame_failure'] = 'unavailable'
    waiting = client.get(endpoint).json()
    assert waiting['state'] == 'waiting' and waiting['provider_id'] == first['provider_id']
    assert client.get('/api/ai/assets?persona=alice').json()['total'] == 0
    behavior['frame_failure'] = ''
    finished = client.get(endpoint).json()
    assert finished['state'] == 'succeeded' and len(finished['additional_assets']) == 1
    assert client.get('/api/ai/assets?persona=alice').json()['total'] == 2
    assert sum(row[0] == 'POST' for row in records) == 1
