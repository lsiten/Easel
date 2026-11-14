"""Web generation, durable queue, model preferences, and owner contracts."""
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sqlite3
import sys

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from easel.generation_models import GenerationError, ImageRequest
from easel.generation_store import GenerationStore
from easel.ownership import owned_media_path
from web import app as web
from tests.generation_support import generation as generation, payload


def test_web_image_generation_registers_owned_files_and_replays_without_billing_twice(generation):
    outputs, records, _ = generation
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        body = payload(n=2, quality='high', style='natural', response_format='b64_json', user='local-creator')
        response = client.post('/api/ai/image/generate', json=body)
        assert response.status_code == 200, response.text
        job = response.json()
        assert job['state'] == 'succeeded' and len(job['assets']) == 2
        assert job['response']['usage']['total_tokens'] == 8
        assert job['response']['created'] == 1730000000 and job['response']['output_format'] == 'png'
        assert [item['revised_prompt'] for item in job['response']['list']] == ['provider-refined-0', 'provider-refined-1']
        assert 'private-receipt-value' not in response.text
        for asset in job['assets']:
            _, path = owned_media_path(outputs, 'alice', asset['path'])
            with Image.open(path) as picture:
                assert picture.size == (3, 2)
        assert client.post('/api/ai/image/generate', json=body).json()['id'] == job['id']
        assert len(records) == 1
        endpoint, authorization, wire = records[0]
        assert endpoint == '/v1/images/generations' and authorization == 'Bearer private-provider-key'
        assert json.loads(wire)['style'] == 'natural' and json.loads(wire)['response_format'] == 'b64_json'
        assert json.loads(wire)['user'] == 'local-creator' and json.loads(wire)['quality'] == 'high'
        assert client.get('/api/ai/assets?persona=alice&page_size=1').json()['total'] == 2
        assert len(client.get('/api/ai/assets?persona=alice&page_size=1').json()['items']) == 1
        assert client.get('/api/ai/logs?persona=alice&state=succeeded&model=provider-image').json()['total'] == 1
        assert client.get('/api/ai/logs?persona=alice&status=success&type=image&channel=new-api').json()['total'] == 1
        summary = client.get('/api/ai/logs?persona=alice').json()['items'][0]
        assert summary['model'] == 'provider-image' and 'request' not in summary and 'response' not in summary
        assert client.get('/api/ai/logs?persona=alice&status=failed').json()['total'] == 0
        assert client.get('/api/ai/logs?persona=alice&type=video').json()['total'] == 0
        assert 'private-provider-key' not in response.text


def test_completed_edit_replays_after_configuration_is_removed(generation, monkeypatch):
    _, records, _ = generation
    body = payload(image='alice-photos/image.png', mask='alice-photos/image.png')
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        created = client.post('/api/ai/image/edit', json=body).json()
        assert created['state'] == 'succeeded'
        for key in ('IMG_API_KEY', 'OPENAI_API_KEY', 'API_KEY'):
            monkeypatch.setenv(key, '')
        monkeypatch.setenv('IMG_BASE_URL', 'https://api.apimart.ai/v1')
        replayed = client.post('/api/ai/image/edit', json=body)
        assert replayed.status_code == 200 and replayed.json()['id'] == created['id']
        assert replayed.json()['state'] == 'succeeded' and len(records) == 1


def test_model_catalog_uses_persona_preferences_and_executes_frozen_runtime_alias(generation, monkeypatch):
    _, records, _ = generation
    monkeypatch.setenv('IMG_MODEL', 'global-image')
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        preference = {'default_model': 'creator-preset', 'options': {
            'runtimeModel': 'gpt-image-1', 'sizes': ['1024x1024'],
            'api_key': 'private-preference-value', 'maxInputImages': 1}}
        assert client.put('/api/local-user/ai/image?persona=alice', json=preference).status_code == 200
        catalog = client.get('/api/ai/models/image/generation?persona=alice')
        names = [model['name'] for model in catalog.json()]
        assert names == ['global-image', 'creator-preset'] and 'private-preference-value' not in catalog.text
        assert client.get('/api/ai/models/image/generation?persona=bob').json()[0]['name'] == 'global-image'
        body = payload(model='creator-preset', style='natural', response_format='b64_json')
        created = client.post('/api/ai/image/generate', json=body).json()
        assert created['state'] == 'succeeded' and created['runtime_model'] == 'gpt-image-1'
        wire = json.loads(records[0][2])
        assert wire['model'] == 'gpt-image-1' and 'style' not in wire and 'response_format' not in wire
        changed = {**preference, 'options': {'runtimeModel': 'different-model'}}
        client.put('/api/local-user/ai/image?persona=alice', json=changed)
        replayed = client.post('/api/ai/image/generate', json=body).json()
        assert replayed['id'] == created['id'] and replayed['runtime_model'] == 'gpt-image-1' and len(records) == 1


def test_preferred_model_limits_are_checked_before_job_creation(generation):
    outputs, records, _ = generation
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        client.put('/api/local-user/ai/edit?persona=alice', json={
            'default_model': 'single-reference', 'options': {'maxInputImages': 1}})
        result = client.post('/api/ai/image/edit', json=payload(
            model='single-reference', image=['alice-photos/image.png', 'alice-photos/image.png']))
        assert result.status_code == 400 and result.json()['detail'] == 'generation_model_input_limit'
        assert GenerationStore(outputs).list('alice').total == 0
        model = client.get('/api/ai/models/image/edit?persona=alice').json()[-1]
        assert model['name'] == 'single-reference' and model['maxInputImages'] == 1
    assert not records


def test_async_queue_is_executed_by_app_worker_and_persists_across_store_instances(generation):
    outputs, records, observed = generation
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        created = client.post('/api/ai/image/generate/async', json=payload()).json()
        assert created['state'] == 'queued'
        assert observed.wait(10), 'Application worker did not dispatch the durable queued task'
        finished = client.post('/api/ai/image/generate', json=payload()).json()
        assert finished['state'] == 'succeeded' and finished['id'] == created['id']
        assert GenerationStore(outputs).get('alice', created['id']).assets
        assert len(records) == 1


def test_generation_routes_enforce_owner_key_permissions_and_request_identity(generation):
    _, records, _ = generation
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        created = client.post('/api/ai/image/generate', json=payload()).json()
        assert client.get(f'/api/ai/image/task/{created["id"]}?persona=bob').status_code == 404
        assert client.get('/api/ai/assets?persona=bob').json()['items'] == []
        assert client.post('/api/ai/image/generate', json=payload(prompt='另一个请求')).status_code == 409
        assert client.post('/api/ai/image/edit', json=payload(image='bob-photos/image.png', request_key='foreign')).status_code == 403
        token = client.post('/api/access-keys', json={'persona': 'alice', 'name': 'reader', 'permission': 'read'}).json()['key']
    remote = TestClient(web.app, client=('203.0.113.9', 4000), base_url='http://localhost:7860')
    assert remote.get('/api/ai/logs?persona=alice').status_code == 403
    assert remote.get('/api/ai/logs?persona=alice', headers={'x-easel-api-key': token}).status_code == 200
    assert remote.get('/api/ai/logs?persona=bob', headers={'x-easel-api-key': token}).status_code == 403
    assert remote.post('/api/ai/image/generate', json=payload(request_key='reader-write'), headers={'x-easel-api-key': token}).status_code == 403
    assert len(records) == 1


def test_provider_non_image_result_stays_out_of_assets(generation):
    outputs, _, _ = generation
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        result = client.post('/api/ai/image/generate', json=payload(prompt='INVALID_OUTPUT')).json()
        assert result['state'] == 'failed' and result['error'] == 'generation_artifact_invalid'
        assert client.get('/api/ai/assets?persona=alice').json()['total'] == 0
        assert not (outputs / f'AI图片-{result["id"]}' / '.easel.json').exists()


def test_provider_return_count_preserves_valid_images_without_automatic_top_up(generation):
    _, records, _ = generation
    body = payload(prompt='PARTIAL_OUTPUT', n=2)
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        result = client.post('/api/ai/image/generate', json=body).json()
        assert result['state'] == 'succeeded' and len(result['assets']) == 1
        assert result['request']['n'] == 2 and len(result['response']['list']) == 1
        assert client.get('/api/ai/assets?persona=alice').json()['total'] == 1
        replay = client.post('/api/ai/image/generate', json=body).json()
        assert replay['id'] == result['id'] and len(records) == 1


def test_interrupted_process_is_unknown_and_never_automatically_resubmitted(generation):
    outputs, records, _ = generation
    store = GenerationStore(outputs)
    job = store.enqueue(ImageRequest(**payload()))
    command = '''from pathlib import Path
import sys
from easel.generation_store import GenerationStore
from easel.publish_recovery import worker_owner
GenerationStore(Path(sys.argv[1])).claim(worker_owner())
'''
    subprocess.run([sys.executable, '-c', command, str(outputs)], check=True)
    assert store.recover() == 1
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        replay = client.post('/api/ai/image/generate', json=payload()).json()
        assert replay['id'] == job.id and replay['state'] == 'unknown'
        assert client.get('/api/ai/logs?persona=alice&status=generating&type=image').json()['items'][0]['state'] == 'unknown'
    assert not records


def test_concurrent_claim_has_one_owner_and_stale_finish_is_rejected(generation):
    outputs, _, _ = generation
    store = GenerationStore(outputs)
    store.enqueue(ImageRequest(**payload()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(store.claim, ['owner:first', 'owner:second']))
    assert sum(job is not None for job in claims) == 1
    claimed = next(job for job in claims if job is not None)
    with pytest.raises(GenerationError, match='generation_worker_stale'):
        store.finish(claimed, 'someone-else', 'succeeded')


def test_symlinked_generation_store_cannot_expose_another_workspace_history(generation, tmp_path):
    outputs, records, _ = generation
    foreign = GenerationStore(tmp_path / 'foreign-outputs')
    foreign.enqueue(ImageRequest(**payload(prompt='private-other-workspace')))
    (outputs / '_generation').symlink_to(foreign.path.parent, target_is_directory=True)
    client = TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000))
    result = client.get('/api/ai/logs?persona=alice')
    assert result.status_code == 409 and 'private-other-workspace' not in result.text
    assert not records


def test_legacy_generation_table_is_upgraded_before_filtered_summary_reads(generation):
    outputs, records, _ = generation
    directory = outputs / '_generation'
    directory.mkdir()
    legacy = {'id': 'a' * 32, 'persona': 'alice', 'state': 'failed', 'created_at': 100, 'updated_at': 200,
              'request': payload(request_key='legacy-key', model='legacy-model')}
    with sqlite3.connect(directory / 'jobs.sqlite3') as db:
        db.execute('''CREATE TABLE generation_jobs(id TEXT PRIMARY KEY,persona TEXT NOT NULL,
            request_key TEXT NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL,owner TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL,UNIQUE(persona,request_key))''')
        db.execute('INSERT INTO generation_jobs VALUES(?,?,?,?,?,?,?)',
                   (legacy['id'], 'alice', 'legacy-key', 'b' * 64, 'failed', '', json.dumps(legacy)))
    store = GenerationStore(outputs)
    page = store.list('alice', model='legacy-model', status='failed', kind='image', channel='new-api')
    assert page.total == 1 and page.items[0].id == legacy['id'] and page.items[0].updated_at == 200
    assert store.get('alice', legacy['id']).request.prompt == legacy['request']['prompt']
    assert GenerationStore(outputs).list('alice').total == 1 and not records
