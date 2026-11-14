"""Native wire protocol and structured configuration regression scenarios."""
import base64
import io
import json
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import anyio
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from easel.generation_app_config import effective_config, revision, save_config, effective_settings
from easel.generation_config_models import AppConfig, ConfigUpdate
from easel.generation_images import ImageGeneration
from easel.generation_models import ImageRequest, GenerationError
from easel.generation_catalog import image_models
from easel.generation_draft_runtime import NativeDraftRuntime
from web import app as web
from tests.generation_support import generation as generation


def configuration():
    return AppConfig.model_validate({'providers': {'gemini': {'apiKey': 'native-private'}},
        'models': {'image': {'generation': [
            {'name': 'native-one', 'channel': 'gemini', 'runtimeModel': 'native-runtime',
             'supportedAspectRatios': ['9:16'], 'resolutions': ['2K']},
            {'name': 'native-two', 'channel': 'gemini'}],
            'edit': [{'name': 'native-one', 'channel': 'gemini', 'supportedAspectRatios': ['9:16']}]}}})


def test_structured_config_mask_cas_secret_preserve_clear_and_environment(generation, monkeypatch):
    outputs, _, _ = generation
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        before = client.get('/api/config').json()
        config = configuration().model_dump(mode='json')
        config['providers']['gemini']['apiKey'] = 'native-private'
        assert client.post('/api/ai/config/validate', json={'config': config}).status_code == 200
        response = client.put('/api/ai/config', json={'config': config, 'revision': before['revision']})
        assert response.status_code == 200, response.text
        saved = response.json()
        assert 'native-private' not in response.text and saved['config']['providers']['gemini']['apiKey'] == ''
        assert client.put('/api/config', json={'config': config, 'revision': before['revision']}).status_code == 409
        assert stat.S_IMODE((outputs / '_generation/app-config.json').stat().st_mode) == 0o600
        assert client.put('/api/config', json={'config': saved['config'], 'revision': saved['revision']}).status_code == 200
        assert effective_settings(outputs, {})['GEMINI_API_KEY'] == 'native-private'
        assert client.post('/api/config/restart').json()['mode'] == 'per-request'
        refreshed = client.get('/api/config').json()
        assert client.put('/api/config', json={'config': refreshed['config'], 'revision': refreshed['revision'],
            'clearSecrets': ['providers.gemini.apiKey']}).status_code == 200
        assert effective_config(outputs).providers['gemini'].apiKey.get_secret_value() == ''
        monkeypatch.setenv('GEMINI_API_KEY', 'environment-private')
        refreshed = client.get('/api/config').json()
        assert client.put('/api/config', json={'config': config, 'revision': refreshed['revision']}).status_code == 409
        config['providers']['gemini']['baseUrl'] = 'file:///etc/passwd'
        assert client.post('/api/config/validate', json={'config': config}).status_code == 422


def test_native_gemini_images_data_references_receipts_and_replay(generation):
    outputs, _, _ = generation
    save_config(outputs, ConfigUpdate(config=configuration(), revision=revision(outputs)))
    calls = []
    picture = io.BytesIO()
    Image.new('RGB', (8, 6), 'blue').save(picture, format='PNG')
    encoded = base64.b64encode(picture.getvalue()).decode()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return
        def do_POST(self):
            calls.append((self.path, self.headers['x-goog-api-key'], json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            if calls[-1][2]['contents'][0]['parts'][-1]['text'] == 'UNCERTAIN':
                self.send_response(503)
                self.end_headers()
                return
            data = {'modelVersion': 'native-version', 'candidates': [{'content': {'parts': [{'inlineData': {'mimeType': 'image/png', 'data': encoded}}]}}],
                    'usageMetadata': {'promptTokenCount': 4, 'candidatesTokenCount': 8, 'totalTokenCount': 12,
                                      'candidatesTokensDetails': [{'modality': 'IMAGE', 'tokenCount': 8}]}}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = {'GEMINI_API_KEY': 'native-private', 'GEMINI_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1beta'}
        service = ImageGeneration(outputs, web.PROJECT_ROOT, settings)
        request = ImageRequest(persona='alice', request_key='native-ref', prompt='Blue cup', model='native-one',
            operation='edit', image='data:image/png;base64,' + encoded, imageSize='2K', aspectRatio='9:16')
        queued = service.submit(request)
        job = anyio.run(service.execute, queued.id)
        assert job.state == 'succeeded' and job.channel == 'gemini'
        assert job.response.model == 'native-version' and job.response.usage.total_tokens == 12
        assert job.response.usage.outputTokenDetails == {'image': 8}
        assert service.submit(request).id == job.id and len(calls) == 1
        path, key, body = calls[0]
        assert path == '/v1beta/models/native-one:generateContent' and key == 'native-private'
        assert body['contents'][0]['parts'][0]['inlineData']['data'] == encoded
        assert body['generationConfig']['imageConfig'] == {'imageSize': '2K', 'aspectRatio': '9:16'}
        assert (outputs / '_generation/receipts' / (job.id + '.native.json')).is_file()
        with Image.open(outputs / job.assets[0].path) as saved:
            assert saved.size == (8, 6)
        catalog = image_models(outputs, 'alice', settings, 'generate')
        assert [item.name for item in catalog] == ['native-one', 'native-two']
        assert NativeDraftRuntime(outputs, web.PROJECT_ROOT, settings).pricing('alice').imageModels[0].supportedAspectRatios == ('9:16',)
        from easel.generation_draft import DraftGeneration
        from easel.generation_draft_models import ImageDraftInput
        draft = DraftGeneration(outputs, NativeDraftRuntime(outputs, web.PROJECT_ROOT, settings))
        batch = draft.submit('alice', ImageDraftInput(prompt='Draft cup', imageModel='native-one', imageCount=1,
            draftType='image', imageSize='2K', aspectRatio='9:16'), 'native-draft')
        completed = anyio.run(draft.execute)
        assert completed.state == 'succeeded' and completed.media[0].generationId
        assert calls[-1][0] == '/v1beta/models/native-runtime:generateContent'
        assert calls[-1][2]['generationConfig']['imageConfig'] == {'imageSize': '2K', 'aspectRatio': '9:16'}
        from fastapi import FastAPI
        from easel.generation_draft_router import create_draft_router
        mounted = FastAPI()
        mounted.include_router(create_draft_router(lambda: draft, lambda request, persona: persona))
        with TestClient(mounted, base_url='http://localhost:7860') as client:
            reference = job.assets[0].path
            for index, image_reference in enumerate((reference, '/api/media/' + reference,
                    'http://localhost:7860/api/media/' + reference)):
                payload = {'prompt': 'Reuse my generated cup', 'imageModel': 'native-one', 'imageCount': 1,
                    'draftType': 'image', 'imageSize': '2K', 'aspectRatio': '9:16', 'imageUrls': [image_reference]}
                created = client.post('/api/ai/draft-generation/image-text?persona=alice', json=payload,
                    headers={'Idempotency-Key': 'owned-native-' + str(index)})
                assert created.status_code == 200, created.text
                owned = anyio.run(draft.execute, created.json()['taskIds'][0])
                assert owned.state == 'succeeded' and owned.media[0].asset.path
                wire = calls[-1][2]['contents'][0]['parts'][0]['inlineData']
                assert wire['mimeType'] == 'image/png' and base64.b64decode(wire['data']) == (outputs / reference).read_bytes()
            count = len(calls)
            foreign = client.post('/api/ai/draft-generation/image-text?persona=alice', json={**payload,
                'imageUrls': ['http://localhost:7860/api/media/bob-photos/image.png']})
            assert foreign.status_code == 403 and len(calls) == count
            private = client.post('/api/ai/draft-generation/image-text?persona=alice', json={**payload,
                'imageUrls': ['http://127.0.0.1:9999/api/media/alice-photos/image.png']})
            assert private.status_code == 400 and len(calls) == count
        uncertain = ImageRequest(persona='alice', request_key='uncertain', prompt='UNCERTAIN', model='native-two')
        pending = service.submit(uncertain)
        result = anyio.run(service.execute, pending.id)
        count = len(calls)
        assert result.state == 'unknown'
        assert service.submit(uncertain).state == 'unknown'
        assert anyio.run(service.execute, pending.id) is None and len(calls) == count
        save_config(outputs, ConfigUpdate(config=AppConfig(), revision=revision(outputs)))
        assert ImageGeneration(outputs, web.PROJECT_ROOT, {}).submit(uncertain).state == 'unknown'
        save_config(outputs, ConfigUpdate(config=configuration(), revision=revision(outputs)))
        with pytest.raises(GenerationError, match='generation_model_option_invalid'):
            service.submit(ImageRequest(persona='alice', request_key='bad-ratio', prompt='x', model='native-one', aspectRatio='1:1'))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_legacy_secret_status_blank_preservation_and_clear(generation, monkeypatch):
    outputs, _, _ = generation
    web.ENV_FILE.write_text('GEMINI_API_KEY=legacy-private\nGEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta\n')
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        initial = client.get('/api/config').json()
        assert 'providers.gemini.apiKey' in initial['configuredSecrets']
        assert 'legacy-private' not in json.dumps(initial)
        saved = client.put('/api/config', json={'revision': initial['revision'], 'config': initial['config']})
        assert saved.status_code == 200
        assert web._generation_settings()['GEMINI_API_KEY'] == 'legacy-private'
        status = saved.json()
        cleared = client.put('/api/config', json={'revision': status['revision'], 'config': status['config'],
            'clearSecrets': ['providers.gemini.apiKey']})
        assert cleared.status_code == 200
        assert 'providers.gemini.apiKey' not in cleared.json()['configuredSecrets']
        assert web._generation_settings()['GEMINI_API_KEY'] == ''
        monkeypatch.setenv('GEMINI_API_KEY', 'locked-private')
        status = client.get('/api/config').json()
        assert 'providers.gemini.apiKey' in status['configuredSecrets'] and 'GEMINI_API_KEY' in status['locked']


def test_catalog_configuration_rejects_invalid_shape_and_preserves_multiple_models(generation):
    from pydantic import ValidationError
    from easel.generation_chat_config import chat_models
    from easel.generation_video_catalog import video_models
    outputs, _, _ = generation
    data = {'models': {'chat': [{'name': 'chat-one', 'inputModalities': ['image']}, {'name': 'chat-two'}],
        'video': {'generation': [{'name': 'video-one', 'channel': 'openai', 'aspectRatios': ['9:16'],
            'runtimeModels': [{'model': 'runtime-video', 'mode': 'text2video'}], 'queuePriority': 1001},
            {'name': 'video-two', 'channel': 'relay'}]}}}
    save_config(outputs, ConfigUpdate(config=AppConfig.model_validate(data), revision=revision(outputs)))
    assert [item.name for item in chat_models(outputs, 'alice', {})] == ['chat-one', 'chat-two']
    videos = video_models(outputs, 'alice', {})
    assert [item.name for item in videos] == ['video-one', 'video-two']
    assert videos[0].ratios == ('9:16',) and videos[0].runtime_models[0].model == 'runtime-video'
    with pytest.raises(ValidationError):
        AppConfig.model_validate({'models': {'video': {'generation': [{'name': 'bad', 'unknownOption': True}]}}})
    with pytest.raises(ValidationError):
        AppConfig.model_validate({'models': {'chat': [{'name': 'bad', 'inputModalities': ['invented']}]}})


def test_gemini_named_openai_relay_model_keeps_openai_protocol(generation):
    outputs, calls, _ = generation
    settings = web._generation_settings()
    settings['GEMINI_API_KEY'] = 'also-configured-native-key'
    service = ImageGeneration(outputs, web.PROJECT_ROOT, settings)
    request = ImageRequest(persona='alice', request_key='relay-gemini', prompt='Blue cup',
        model='gemini-relay-name', channel='openai')
    queued = service.submit(request)
    completed = anyio.run(service.execute, queued.id)
    assert completed.state == 'succeeded' and completed.channel == 'openai'
    assert calls[0][0] == '/v1/images/generations'
    assert json.loads(calls[0][2])['model'] == 'gemini-relay-name'
