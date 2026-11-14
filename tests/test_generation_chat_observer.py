"""Ordinary chat stays the sole producer while entering durable Agent history."""
from concurrent.futures import ThreadPoolExecutor
import json

from easel.generation_agent_store import AgentStore
from easel.generation_store import GenerationStore
from tests.test_generation_runtime_integration import runtime  # noqa: F401 -- shared real-app fixture
from web import app as web


def test_ordinary_chat_and_agent_resume_share_native_session(runtime):
    client, outputs, recorded, _, _ = runtime
    for turn in ('first', 'second'):
        result = client.post('/api/chat/stream', json={'persona': 'alice', 'sessionId': 'ordinary',
            'turnId': turn, 'message': turn})
        assert result.status_code == 200 and 'answer' in result.text
    tasks = client.get('/api/ai/agent/tasks?persona=alice').json()
    assert tasks['total'] == 1 and len(recorded) == 2
    task = tasks['items'][0]
    run = AgentStore(GenerationStore(outputs)).run('alice', task['id'])
    assert run.turn_id == 'second' and run.session_id == 'ordinary'
    assert client.post('/api/ai/agent/tasks?persona=alice', json={'taskId': task['id'], 'prompt': 'continue'}).status_code == 200
    assert len(recorded) == 3 and recorded[-1].native_id == 'test-native-session'
    assert client.get('/api/ai/agent/tasks?persona=alice').json()['total'] == 1
    logs = client.get('/api/ai/logs?persona=alice&type=agent').json()
    assert logs['total'] == 1 and logs['items'][0]['channel'] == 'codex'


def test_ordinary_question_and_abort_use_original_runtime(runtime):
    client, outputs, recorded, started, behavior = runtime
    behavior['ask'] = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, '/api/chat/stream', json={'persona': 'alice', 'sessionId': 'question', 'turnId': 'question-turn', 'message': 'ask'})
        assert started.wait(10)
        task = AgentStore(GenerationStore(outputs)).list('alice', page=1, size=20, keyword='', favorite=False).items[0]
        messages = client.get(f'/api/ai/agent/tasks/{task.id}/messages?persona=alice').json()
        question = next(json.loads(event['data']) for event in messages['messages'] if event['type'] == 'question')
        result = client.post(f'/api/ai/agent/tasks/{task.id}/questions/{question["id"]}?persona=alice', json={'answers': {'approval': ['approved']}})
        assert result.status_code == 200 and result.json()['ok']
        assert pending.result(10).status_code == 200
    behavior.update(ask=False, wait=True)
    started.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, '/api/chat/stream', json={'persona': 'alice', 'sessionId': 'question', 'turnId': 'abort-turn', 'message': 'wait'})
        assert started.wait(10)
        assert client.post(f'/api/ai/agent/tasks/{task.id}/abort?persona=alice').status_code == 200
        assert pending.result(10).status_code == 200
    assert client.get(f'/api/ai/agent/tasks/{task.id}?persona=alice').json()['status'] == 'aborted'
    assert len(recorded) == 2


def test_ordinary_direct_api_is_recorded_without_harness(runtime, monkeypatch):
    client, _, recorded, _, _ = runtime
    monkeypatch.setattr(web, '_chat_backend', lambda session, persona: 'api')
    calls = []
    async def stream(settings, content, **kwargs):
        calls.append(content)
        yield 'token', 'direct answer'
        yield 'finish', 'stop'
    monkeypatch.setattr(web, 'stream_chat', stream)
    result = client.post('/api/chat/stream', json={'persona': 'alice', 'sessionId': 'direct', 'turnId': 'direct-turn', 'message': 'hello'})
    assert result.status_code == 200 and 'direct answer' in result.text
    tasks = client.get('/api/ai/agent/tasks?persona=alice').json()
    assert tasks['total'] == 1 and tasks['items'][0]['status'] == 'completed'
    assert len(calls) == 1 and not recorded
    assert client.post('/api/ai/agent/tasks?persona=alice', json={'taskId': tasks['items'][0]['id'], 'prompt': 'again'}).status_code == 200
    assert len(calls) == 2 and not recorded
    assert client.get('/api/ai/logs?persona=alice&type=agent').json()['total'] == 1


def test_general_chat_keeps_unscoped_native_session(runtime, monkeypatch):
    client, outputs, recorded, _, _ = runtime
    monkeypatch.setattr(web, '_persona_prefix', lambda persona: '')
    result = client.post('/api/chat/stream', json={'sessionId': 'generic', 'turnId': 'generic-turn', 'message': 'hello'})
    assert result.status_code == 200 and 'answer' in result.text
    tasks = client.get('/api/ai/agent/tasks?persona=').json()
    assert tasks['total'] == 1 and tasks['items'][0]['userId'] == ''
    identifier = tasks['items'][0]['id']
    run = AgentStore(GenerationStore(outputs)).run('', identifier)
    assert run.session_id == 'generic'
    assert client.post('/api/ai/agent/tasks?persona=', json={'taskId': identifier, 'prompt': 'continue'}).status_code == 200
    assert len(recorded) == 2 and recorded[-1].native_id == 'test-native-session'
    assert client.get('/api/ai/logs?persona=&type=agent').json()['total'] == 1
    assert client.get('/api/ai/agent/tasks?persona=alice').json()['total'] == 0


def test_ordinary_media_quote_and_reference_reach_same_producer_and_history(runtime):
    client, outputs, recorded, _, _ = runtime
    uploaded = client.post('/api/upload', data={'persona': 'alice', 'sessionId': 'media'},
                           files={'files': ('context.txt', b'attachment context', 'text/plain')})
    assert uploaded.status_code == 200
    attachment = uploaded.json()['files'][0]
    result = client.post('/api/chat/stream', json={'persona': 'alice', 'sessionId': 'media', 'turnId': 'media-turn',
        'message': 'describe', 'attachments': [attachment], 'quote': 'quoted context',
        'references': [{'kind': 'asset', 'id': 'reference-id', 'label': 'reference label', 'detail': 'reference detail'}]})
    assert result.status_code == 200 and len(recorded) == 1
    assert attachment['path'] in recorded[0].prompt and 'quoted context' in recorded[0].prompt and 'reference detail' in recorded[0].prompt
    task = AgentStore(GenerationStore(outputs)).list('alice', page=1, size=20, keyword='', favorite=False).items[0]
    detail = client.get(f'/api/ai/agent/tasks/{task.id}?persona=alice').json()
    context = next(event['data'] for event in detail['messages'] if event['type'] == 'context')
    assert context['attachments'] == [attachment] and context['quote'] == 'quoted context'
    assert context['references'][0]['id'] == 'reference-id'
    share = client.post(f'/api/ai/agent/tasks/{task.id}/share?persona=alice', json={}).json()
    public = client.get('/api/ai/agent/tasks/shared/' + share['token'])
    assert attachment['path'] not in public.text and 'quoted context' not in public.text
