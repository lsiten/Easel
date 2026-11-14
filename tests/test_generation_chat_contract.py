"""Real HTTP/SSE provider, FastAPI boundary, and durable SQLite contracts."""
import base64
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from PIL import Image
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from easel.generation_chat_router import create_router
from easel.generation_chat_service import ChatGeneration
from easel.generation_store import GenerationStore
from easel.local_access_router import validate_caller_key
from easel.local_access import AccessStore


@pytest.fixture
def surface(tmp_path, request):
    calls = []
    image = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(image, 'PNG')
    inline = 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):
            if getattr(request, 'param', 'SECRET-token') == '':
                assert self.headers.get('Authorization') is None
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            text = body['messages'][0]['content']
            self.send_response(401 if text == 'reject' else 200)
            self.send_header('Content-Type', 'text/event-stream' if body['stream'] else 'application/json')
            self.end_headers()
            if text == 'reject':
                self.wfile.write(b'{"error":"SECRET-token"}')
            elif not body['stream']:
                result = {'choices': [{'message': {'content': inline if text == 'image' else 'answer'}}],
                          'usage': {'prompt_tokens': 8, 'completion_tokens': 3, 'total_tokens': 11}}
                self.wfile.write(json.dumps(result).encode())
            elif self.path.endswith('/messages'):
                for event in [{'type': 'message_start', 'message': {'usage': {'input_tokens': 8}}},
                              {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'hello'}},
                              {'type': 'message_delta', 'usage': {'output_tokens': 3}}, {'type': 'message_stop'}]:
                    self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
            else:
                self.wfile.write(b'data: {"choices":[{"delta":{"tool_calls":[{"id":"t","function":{"name":"lookup","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}\n\n')
                if text != 'cut':
                    self.wfile.write(b'data: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3,"total_tokens":11,"prompt_cache_hit_tokens":2}}\n\ndata: [DONE]\n\n')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = {'EASEL_DIRECT_API_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                'EASEL_DIRECT_API_MODEL': 'test-model', 'EASEL_DIRECT_API_KEY': getattr(request, 'param', 'SECRET-token'),
                'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1', 'ANTHROPIC_MODEL': 'claude-test'}
    store = GenerationStore(tmp_path)
    app = FastAPI()

    @app.middleware('http')
    async def keys(request, call_next):
        try:
            await validate_caller_key(request, tmp_path)
        except HTTPException as error:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': error.detail}, status_code=error.status_code)
        return await call_next(request)

    def access(request):
        if not getattr(request.state, 'caller_key', None) and request.headers.get('x-local') != 'yes':
            raise HTTPException(403)

    app.include_router(create_router(lambda: ChatGeneration(store, settings), lambda name: name in ('alice', 'bob'), access))
    with TestClient(app, headers={'x-local': 'yes'}) as client:
        yield client, store, calls, tmp_path
    server.shutdown()
    server.server_close()
    thread.join()


def payload(text='hello', key='one', model='test-model'):
    return {'persona': 'alice', 'request_key': key, 'model': model, 'messages': [{'role': 'user', 'content': text}]}


@pytest.mark.parametrize('surface', [''], indirect=True)
def test_local_provider_without_key_uses_valid_http_headers(surface):
    client, _, calls, _ = surface
    response = client.post('/api/ai/chat', json=payload())
    assert response.status_code == 200 and response.json()['content'] == 'answer'
    assert len(calls) == 1


def test_completion_options_usage_and_idempotent_history(surface):
    client, store, calls, _ = surface
    request = {**payload(), 'maxTokens': 50, 'topP': .5, 'modelKwargs': {'seed': 7}}
    first = client.post('/api/ai/chat', json=request)
    second = client.post('/api/ai/chat', json=request)
    assert first.json() == second.json() == {'content': 'answer', 'model': 'test-model', 'usage': {'input_tokens': 8, 'output_tokens': 3, 'total_tokens': 11}}
    assert len(calls) == 1 and calls[0]['max_tokens'] == 50 and calls[0]['seed'] == 7
    with store.connect() as db:
        assert db.execute('SELECT state FROM chat_jobs').fetchone()[0] == 'succeeded'
    assert client.post('/api/ai/chat', json=payload('changed')).status_code == 409


@pytest.mark.parametrize('route,model', [('stream', 'test-model'), ('claude', 'claude-test')])
def test_stream_preserves_tools_events_usage_and_replay(surface, route, model):
    client, store, calls, _ = surface
    request = {**payload(model=model), 'tools': [{'name': 'lookup'}]}
    response = client.post('/api/ai/chat/' + route, json=request)
    replay = client.post('/api/ai/chat/' + route, json=request)
    assert response.text == replay.text and len(calls) == 1
    assert ('tool_calls' if route == 'stream' else 'event: message_stop') in response.text
    with store.connect() as db:
        record = json.loads(db.execute('SELECT body FROM chat_jobs').fetchone()[0])
    assert record['state'] == 'succeeded' and record['response']['usage']['output_tokens'] == 3
    assert calls[0]['tools'] == [{'name': 'lookup'}]
    if route == 'claude':
        assert calls[0]['max_tokens'] == 32000
    else:
        assert calls[0]['stream_options'] == {'include_usage': True}
        assert record['response']['usage']['input_tokens'] == 6


@pytest.mark.parametrize('route,text', [('/chat', 'reject'), ('/chat/stream', 'cut'), ('/chat/stream', 'reject')])
def test_ambiguous_errors_never_replay_or_leak_secrets(surface, route, text):
    client, store, calls, _ = surface
    first = client.post('/api/ai' + route, json=payload(text))
    second = client.post('/api/ai' + route, json=payload(text))
    assert 'SECRET' not in first.text and second.status_code == 409 and len(calls) == 1
    with store.connect() as db:
        assert db.execute('SELECT state FROM chat_jobs').fetchone()[0] == 'unknown'


def test_generated_images_are_owned_verified_assets(surface):
    client, store, _, root = surface
    result = client.post('/api/ai/chat', json=payload('image')).json()
    assert result['content'].startswith('/api/media/') and 'base64' not in result['content']
    assets = store.assets('alice')
    assert assets.total == 1 and store.assets('bob').total == 0
    with Image.open(root / assets.items[0].path) as picture:
        assert picture.size == (2, 2)


def test_catalog_scope_and_write_keys(surface):
    client, _, calls, root = surface
    assert [item['name'] for item in client.get('/api/ai/models/chat?persona=alice&channel=anthropic').json()] == ['claude-test']
    assert client.get('/api/ai/models/chat?persona=alice&scene=missing').json() == []
    key = AccessStore(root / '_access').create_key('bob', 'reader', 'read')
    headers = {'x-easel-api-key': key['key']}
    assert client.post('/api/ai/chat', json=payload(), headers=headers).status_code == 403
    assert client.get('/api/ai/models/chat?persona=alice', headers=headers).status_code == 403
    assert calls == []


def test_resume_replays_only_saved_suffix_without_provider_call(surface):
    client, _, calls, _ = surface
    first = client.post('/api/ai/chat/stream', json=payload())
    resumed = client.post('/api/ai/chat/stream', json=payload(), headers={'Last-Event-ID': '1'})
    assert 'id: 1\n' in first.text and 'id: 1\n' not in resumed.text
    assert 'id: 2\n' in resumed.text and '[DONE]' in resumed.text and len(calls) == 1


def test_multimodal_input_and_tool_options_remain_lossless(surface):
    client, _, calls, _ = surface
    content = [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aGVsbG8=', 'detail': 'high'}},
               {'type': 'text', 'text': 'Describe'}]
    response = client.post('/api/ai/chat/stream', json={**payload(content), 'max_completion_tokens': 10,
                           'tool_choice': 'auto', 'parallel_tool_calls': False})
    assert response.status_code == 200 and '[DONE]' in response.text
    assert calls[0]['messages'][0]['content'] == content and calls[0]['parallel_tool_calls'] is False


def test_missing_auth_invalid_model_and_invalid_claude_messages_do_not_call_provider(surface):
    client, _, calls, _ = surface
    assert client.post('/api/ai/chat', json=payload(), headers={'x-local': 'no'}).status_code == 403
    assert client.post('/api/ai/chat', json=payload(model='unconfigured')).status_code == 400
    body = payload(model='claude-test')
    body['messages'][0]['role'] = 'system'
    assert client.post('/api/ai/chat/claude', json=body).status_code == 422
    assert calls == []
