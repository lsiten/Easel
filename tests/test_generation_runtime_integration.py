"""Real app entry points using the existing Harness boundary and durable chat journal."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import threading

import anyio
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from easel.generation_agent_store import AgentStore
from easel.generation_agent_models import CreateTask
from easel.generation_store import GenerationStore
from easel.local_access import AccessStore
from web import app as web


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    env = tmp_path / '.env'
    env.write_text('')
    monkeypatch.setattr(web, 'OUTPUTS_DIR', outputs)
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setattr(web, 'ENV_FILE', env)
    monkeypatch.setattr(web, 'profile_exists', lambda name: name in {'alice', 'bob'})
    monkeypatch.setattr(web, '_persona_prefix', lambda persona: f'Persona {persona}\n')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {'codex': 'configured-model'},
        'sandbox': 'read-only', 'codex_auto_approve': False})
    recorded = []
    started = threading.Event()
    behavior = {'wait': False, 'ask': False}

    class Harness:
        async def run(self, request, emit, ask, bind):
            recorded.append(request)
            bind('test-native-session')
            emit('token', 'answer')
            if behavior['ask']:
                started.set()
                reply = await ask([{'questionId': 'approval', 'question': 'Continue?', 'options': []}])
                emit('token', ' ' + reply['approval'][0])
            if behavior['wait']:
                started.set()
                await anyio.sleep_forever()

    monkeypatch.setattr(web.harnesses, 'adapter', lambda runtime: Harness())
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        yield client, outputs, recorded, started, behavior


def first_task(response):
    for line in response.text.splitlines():
        if line.startswith('data: '):
            data = json.loads(line[6:])
            if data.get('type') == 'init':
                return data['taskId']
    raise AssertionError('task init event missing')


def test_agent_route_uses_real_chat_entry_media_model_resume_and_cleanup(runtime):
    client, outputs, recorded, _, _ = runtime
    buffer = io.BytesIO()
    Image.new('RGB', (2, 3), 'blue').save(buffer, 'PNG')
    image = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
    result = client.post('/api/ai/agent/tasks?persona=alice', json={'model': 'per-turn-model', 'prompt': [
        {'type': 'text', 'text': 'describe this image'}, {'type': 'image', 'source': {'type': 'url', 'url': image}},
        {'type': 'document', 'source': {'type': 'text', 'media_type': 'text/plain', 'data': 'document context'}}]})
    assert result.status_code == 200, result.text
    identifier = first_task(result)
    task = client.get('/api/ai/agent/tasks/' + identifier + '?persona=alice').json()
    assert task['status'] == 'completed' and any(item['type'] == 'assistant' and item['data'] == 'answer' for item in task['messages'])
    assert recorded[0].model == 'per-turn-model' and recorded[0].env['EASEL_PERSONA'] == 'alice'
    assert 'document context' in recorded[0].prompt and 'outputs/_inbox/' in recorded[0].prompt
    files = list((outputs / '_inbox').rglob('attachment.png'))
    assert len(files) == 1 and files[0].read_bytes() == buffer.getvalue()
    resumed = client.post('/api/ai/agent/tasks?persona=alice', json={'taskId': identifier, 'prompt': 'continue'})
    assert resumed.status_code == 200 and first_task(resumed) == identifier and len(recorded) == 2
    assert recorded[1].native_id == 'test-native-session' and recorded[1].model == 'configured-model'
    log = client.get('/api/ai/logs?persona=alice&type=agent&status=success').json()
    assert log['total'] == 1 and log['items'][0]['channel'] == 'codex'
    share = client.post('/api/ai/agent/tasks/' + identifier + '/share?persona=alice', json={}).json()
    public = client.get('/api/ai/agent/tasks/shared/' + share['token']).json()
    assert any(message['text'] == 'describe this image' for message in public['messages'])
    assert 'data:image' not in json.dumps(public) and '_inbox' not in json.dumps(public)
    assert client.get('/api/ai/agent/tasks/' + identifier + '?persona=bob').status_code == 404
    assert client.delete('/api/ai/agent/tasks/' + identifier + '?persona=alice').status_code == 200
    assert client.get('/api/ai/agent/tasks/shared/' + share['token']).status_code == 404
    assert not list((web.SESSIONS_DIR / 'jobs').glob('*.jsonl'))


def test_agent_abort_stops_actual_named_runtime_turn(runtime):
    client, outputs, recorded, started, behavior = runtime
    behavior['wait'] = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, '/api/ai/agent/tasks?persona=alice', json={'prompt': 'wait'})
        assert started.wait(10)
        task = AgentStore(GenerationStore(outputs)).list('alice', page=1, size=1, keyword='', favorite=False).items[0]
        result = client.post('/api/ai/agent/tasks/' + task.id + '/abort?persona=alice')
        assert result.status_code == 200, result.text
        assert pending.result(timeout=10).status_code == 200
    assert client.get('/api/ai/agent/tasks/' + task.id + '?persona=alice').json()['status'] == 'aborted'
    assert len(recorded) == 1 and not web._RUNNING_CHAT


def test_internal_alias_owner_and_read_only_agent_key_guards(runtime):
    client, outputs, recorded, _, _ = runtime
    keys = AccessStore(outputs / '_access')
    write = {'x-easel-api-key': keys.create_key('alice', 'writer', 'write')['key']}
    read = {'x-easel-api-key': keys.create_key('alice', 'reader', 'read')['key']}
    assert client.post('/api/ai/agent/tasks?persona=alice', headers=read, json={'prompt': 'forbidden'}).status_code == 403
    assert client.post('/api/internal/ai/draft-generation/task', headers=write,
                       json={'userId': 'bob', 'userType': 'admin', 'taskId': 'foreign'}).status_code == 403
    assert client.post('/api/internal/ai/draft-generation/task', headers=write,
                       json={'userId': 'alice', 'userType': 'system', 'taskId': 'missing'}).status_code == 404
    assert not recorded


def test_agent_question_reply_is_bound_to_current_task_and_owner(runtime):
    client, outputs, recorded, started, behavior = runtime
    behavior['ask'] = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, '/api/ai/agent/tasks?persona=alice', json={'prompt': 'ask before continuing'})
        assert started.wait(10)
        task = AgentStore(GenerationStore(outputs)).list('alice', page=1, size=1, keyword='', favorite=False).items[0]
        messages = client.get('/api/ai/agent/tasks/' + task.id + '/messages?persona=alice').json()
        question = next(json.loads(item['data']) for item in messages['messages'] if item['type'] == 'question')
        endpoint = '/api/ai/agent/tasks/' + task.id + '/questions/' + question['id']
        answer = {'answers': {'approval': ['approved']}}
        assert client.post(endpoint + '?persona=bob', json=answer).status_code == 404
        accepted = client.post(endpoint + '?persona=alice', json=answer)
        assert accepted.status_code == 200 and accepted.json()['ok'] is True
        assert pending.result(timeout=10).status_code == 200
    detail = client.get('/api/ai/agent/tasks/' + task.id + '?persona=alice').json()
    assert detail['status'] == 'completed' and detail['messages'][-1]['data'] == 'answer approved' and len(recorded) == 1


def test_agent_logs_include_task_before_runtime_checkpoint(runtime):
    client, outputs, _, _, _ = runtime
    # Initialize the real journal through a completed run, then simulate a task
    # created just before a process died without writing its runtime receipt.
    assert client.post('/api/ai/agent/tasks?persona=alice', json={'prompt': 'first'}).status_code == 200
    run = AgentStore(GenerationStore(outputs)).claim('alice', CreateTask(prompt='not started'))
    result = client.get('/api/ai/logs?persona=alice&type=agent')
    assert result.status_code == 200, result.text
    orphan = next(item for item in result.json()['items'] if item['id'] == run.task_id)
    assert orphan['state'] == 'running' and orphan['channel'] == 'agent' and orphan['model'] == ''


def test_generation_admin_settings_save_mask_secrets_and_guard_access(runtime, monkeypatch):
    client, outputs, _, _, _ = runtime
    for name in ('GROK_API_KEY', 'GROK_BASE_URL', 'EASEL_DRAFT_MODEL_RATES'):
        monkeypatch.delenv(name, raising=False)
    secret = 'config-test-private-value'
    result = client.post('/api/generation-settings', json={'values': {'GROK_API_KEY': secret,
        'GROK_BASE_URL': 'https://provider.example/v1',
        'EASEL_DRAFT_MODEL_RATES': '{"video":[{"amount":0.02,"currency":"USD","unit":"second"}]}'}})
    assert result.status_code == 200, result.text
    field = next(item for item in result.json()['fields'] if item['name'] == 'GROK_API_KEY')
    assert field['configured'] is True and field['value'] == '' and secret not in result.text
    assert web.read_settings(web.ENV_FILE)['GROK_API_KEY'] == secret
    invalid = client.post('/api/generation-settings', json={'values': {'GROK_API_KEY': {'private': secret}}})
    assert invalid.status_code == 422 and secret not in invalid.text
    assert client.post('/api/generation-settings', json={'values': {'EASEL_DRAFT_MODEL_RATES': '{"video":[] invalid}'}}).status_code == 400
    assert client.post('/api/generation-settings', json={'values': {'GROK_BASE_URL': 'https://user:secret@example.com/'}}).status_code == 400
    monkeypatch.setenv('GROK_API_KEY', 'process-owned-test-value')
    assert client.post('/api/generation-settings', json={'values': {'GROK_API_KEY': 'replacement'}}).status_code == 409
    key = AccessStore(outputs / '_access').create_key('alice', 'writer', 'write')['key']
    assert client.get('/api/generation-settings', headers={'x-easel-api-key': key}).status_code == 403
