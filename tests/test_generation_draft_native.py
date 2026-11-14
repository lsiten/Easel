"""Native structured planner HTTP and real reference-video frame extraction."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import json
import base64
import io
from PIL import Image
import shutil
import subprocess
import anyio
import httpx
import pytest
from fastapi import FastAPI
from easel.generation_draft import DraftGeneration
from easel.generation_draft_models import ImageDraftInput, URLDraftInput
from easel.generation_app_config import revision, save_config
from easel.generation_config_models import AppConfig, ConfigUpdate
from easel.generation_draft_router import create_draft_router
from easel.generation_draft_runtime import NativeDraftRuntime
from easel.generation_models import GenerationError


@contextmanager
def provider(video_data: bytes = b''):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.end_headers()
            self.wfile.write(video_data)
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            if self.path.endswith('/videos'):
                calls.append({'video_submission': True})
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"id":"video-result","status":"completed","created_at":1700000000}')
                return
            body = json.loads(raw)
            calls.append(body)
            if self.path.endswith('/images/generations'):
                output = io.BytesIO()
                Image.new('RGB', (24, 24), 'blue').save(output, format='PNG')
                payload = {'created': 1700000000, 'data': [{'b64_json': base64.b64encode(output.getvalue()).decode()}]}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())
                return
            system = body['messages'][0]['content']
            result = {'items': [{'text': 'Use concise workshop captions.'}]} if 'Summarize' in system else {
                'title': 'Clay workshop', 'description': 'Shape your first clay cup.', 'topics': ['pottery'],
                'videoPrompt': 'A potter shapes a cup.', 'imagePrompts': ['A blue ceramic cup on a wooden table.']}
            if 'Summarize' not in system and isinstance(body['messages'][1]['content'], list):
                evidence = ' '.join(part.get('text', '') for part in body['messages'][1]['content'])
                if 'tickets cost twenty dollars' in evidence and 'Do not describe this event as free.' in evidence:
                    result['description'] = 'Sunday workshop. Tickets: $20.'
            payload = {'choices': [{'message': {'content': json.dumps(result)}}],
                       'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = {'EASEL_DIRECT_API_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                'EASEL_DIRECT_API_KEY': 'fixture-key', 'EASEL_DIRECT_API_MODEL': 'planner', 'IMG_MODEL': 'local-image', 'IMG_API_KEY': 'fixture-key',
                'IMG_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                'VIDEO_MODEL': 'local-video', 'VIDEO_API_KEY': 'fixture-key',
                'VIDEO_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1'}
    try:
        yield settings, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_native_planner_memory_and_configured_pricing(tmp_path: Path) -> None:
    tmp_path = tmp_path / 'outputs'
    with provider() as (settings, calls):
        settings['EASEL_DRAFT_MODEL_RATES'] = json.dumps({'local-image': [{'amount': 0.03, 'unit': 'image', 'currency': 'USD'}]})
        async def scenario() -> None:
            runtime = NativeDraftRuntime(tmp_path, Path.cwd(), settings)
            service = DraftGeneration(tmp_path, runtime)
            app = FastAPI()
            app.include_router(create_draft_router(lambda: service, lambda request, persona: persona or 'alice'))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                payload = {'prompt': 'A clay workshop', 'imageModel': 'local-image', 'imageCount': 1}
                created = await client.post('/api/ai/draft-generation/image-text', json=payload)
                assert created.status_code == 200
                identifier = created.json()['taskIds'][0]
                await service.execute(identifier)
                result = (await client.get('/api/ai/draft-generation/'+identifier)).json()
                assert result['status'] == 'success' and result['response']['title'] == 'Clay workshop'
                memory = await client.post('/api/ai/draft-generation/memory/regenerate', json={'contentType': 'image-text'})
                assert memory.json()['items'][0]['text'] == 'Use concise workshop captions.'
                prices = (await client.get('/api/ai/draft-generation/pricing')).json()
                assert prices['imageModels'][0]['pricing'][0]['amount'] == 0.03
                assert prices['imageEditModels'][0]['model'] == 'local-image'
                assert prices['videoModels'][0]['pricing'][0]['amount'] is None
                with service.store.base.connect() as db:
                    rows = db.execute('SELECT state,body FROM chat_jobs').fetchall()
                assert len(rows) == 2 and all(row['state'] == 'succeeded' for row in rows)
                assert len(calls) == 3
                with pytest.raises(GenerationError):
                    service.submit('alice', ImageDraftInput(prompt='cup', imageModel='unknown'), 'bad')
        anyio.run(scenario)


def test_video_url_planner_rejects_text_only_model_before_import(tmp_path: Path) -> None:
    outputs = tmp_path / 'outputs'
    config = AppConfig.model_validate({
        'models': {'chat': [{'name': 'text-only', 'channel': 'openai', 'inputModalities': ['text']}]},
    })
    save_config(outputs, ConfigUpdate(config=config, revision=revision(outputs)))
    settings = {
        'EASEL_DIRECT_API_BASE_URL': 'http://127.0.0.1:9/v1',
        'EASEL_DIRECT_API_KEY': 'fixture-key',
        'EASEL_DIRECT_API_MODEL': 'planner',
    }
    service = DraftGeneration(outputs, NativeDraftRuntime(outputs, Path.cwd(), settings))
    with pytest.raises(GenerationError, match='draft_planner_model_requires_vision'):
        service.submit('alice', URLDraftInput(
            videoUrl='https://93.184.216.34/video.mp4',
            plannerModel='text-only',
        ), 'vision-required')


def test_native_video_url_import_analyzes_real_frames(tmp_path: Path, monkeypatch) -> None:
    fixture = tmp_path/'fixture.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=96x96:d=1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(fixture)], check=True, capture_output=True)
    def download(url, destination, **kwargs):
        shutil.copyfile(fixture, destination)
    monkeypatch.setattr('easel.content_library_import.download_to_file', download)
    with provider() as (settings, calls):
        async def scenario() -> None:
            runtime = NativeDraftRuntime(tmp_path, Path.cwd(), settings)
            service = DraftGeneration(tmp_path, runtime)
            app = FastAPI()
            app.include_router(create_draft_router(lambda: service, lambda request, persona: 'alice'))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/api/ai/draft-generation/from-video-url', json={'videoUrl': 'https://93.184.216.34/video.mp4'}, headers={'Idempotency-Key': 'video-url'})
                assert response.status_code == 200, response.text
                repeated = await client.post('/api/ai/draft-generation/from-video-url', json={'videoUrl': 'https://93.184.216.34/video.mp4'}, headers={'Idempotency-Key': 'video-url'})
                assert response.json() == repeated.json() and len(calls) == 1
                job = service.store.list('alice').items[0]
                assert job.response.coverUrl and (tmp_path/job.media[0].thumbnail).is_file()
                assert sum(part['type'] == 'image_url' for part in calls[0]['messages'][1]['content']) == 6
                blocked = await client.post('/api/ai/draft-generation/from-video-url', json={'videoUrl': 'http://127.0.0.1/private.mp4'})
                assert blocked.status_code == 400
        anyio.run(scenario)


def test_native_video_pipeline_archives_and_projects(tmp_path: Path) -> None:
    fixture = tmp_path/'fixture.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=96x96:d=1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(fixture)], check=True, capture_output=True)
    with provider(fixture.read_bytes()) as (settings, calls):
        async def scenario() -> None:
            service = DraftGeneration(tmp_path, NativeDraftRuntime(tmp_path, Path.cwd(), settings))
            app = FastAPI()
            app.include_router(create_draft_router(lambda: service, lambda request, persona: 'alice'))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                made = await client.post('/api/ai/draft-generation/v2', json={'model': 'local-video', 'prompt': 'pottery', 'duration': 4})
                assert made.status_code == 200
                identifier = made.json()['taskIds'][0]
                await service.execute(identifier)
                job = (await client.get('/api/ai/draft-generation/'+identifier)).json()
                assert job['status'] == 'success', job
                assert job['response']['materialId'] and job['response']['coverUrl']
                assert (tmp_path/job['media'][0]['asset']['path']).read_bytes() == fixture.read_bytes()
                assert sum(call.get('video_submission', False) for call in calls) == 1
        anyio.run(scenario)
