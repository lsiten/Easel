"""Observe real lifecycle transitions and full ASGI streaming lifetimes."""
from pathlib import Path
import sqlite3
import time
import anyio
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import httpx
from easel.generation_store import GenerationStore
from easel.generation_models import ImageRequest
from easel.generation_video_store import VideoStore
from easel.generation_video_models import OpenAIVideoRequest, VideoTarget
from easel.http_metrics import HttpMetrics, HttpMetricsMiddleware
from easel.publish_recovery import worker_owner
from easel.queue_metrics import render_queues


def test_durable_queue_counts_timings_and_metadata_only_updates(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path)
    job = store.enqueue(ImageRequest(persona='private-user', request_key='secret-key', model='private-model', prompt='private-prompt'))
    time.sleep(0.02)
    owner = worker_owner()
    claimed = store.claim(owner, job.id)
    time.sleep(0.02)
    store.record_inputs(claimed, owner, ())
    store.finish(claimed, owner, 'succeeded')
    reopened = GenerationStore(tmp_path)
    with reopened.connect() as db:
        output = '\n'.join(render_queues(db))
        assert 'easel_queue_started_total{queue="image"} 1.0' in output
        assert 'easel_queue_completed_total{queue="image"} 1.0' in output
        assert 'easel_queue_attempts_total{queue="image"} 1.0' in output
        assert 'easel_queue_jobs{queue="image",state="succeeded"} 1' in output
        measured = dict(db.execute("SELECT metric,value FROM operational_observations WHERE metric LIKE '%seconds'"))
        assert measured['waiting_seconds'] >= 0.015
        assert measured['processing_seconds'] >= 0.015
    assert not any(secret in output for secret in ('private-user', 'secret-key', 'private-model', 'private-prompt', job.id))


def test_video_polling_is_one_paid_attempt_with_full_lifecycle_duration(tmp_path: Path) -> None:
    store = VideoStore(GenerationStore(tmp_path))
    job = store.enqueue(OpenAIVideoRequest(persona='alice', request_key='one', prompt='clay'), VideoTarget(base='https://example.invalid/v1', model='sora-2'))
    owner = worker_owner()
    claimed = store.claim(owner, job.id)
    for _ in range(3):
        store.save(claimed.model_copy(update={'provider_id': 'native-id'}), owner, 'waiting')
        time.sleep(0.01)
        claimed = store.claim(owner, job.id)
    store.save(claimed, owner, 'failed')
    with store.generation.connect() as db:
        metrics = dict(db.execute("SELECT metric,sum(value) FROM operational_observations WHERE queue='video' GROUP BY metric"))
        assert metrics['started'] == metrics['attempts'] == metrics['failed'] == 1
        assert metrics['processing_seconds'] >= 0.025
        assert db.execute("SELECT count(*) FROM operational_observations WHERE metric='processing_seconds'").fetchone()[0] == 1


def test_streaming_response_counts_after_final_body_and_uses_route_template(tmp_path: Path) -> None:
    async def scenario() -> None:
        metrics = HttpMetrics()
        app = FastAPI()
        app.add_middleware(HttpMetricsMiddleware, metrics=metrics)
        began, release = anyio.Event(), anyio.Event()

        @app.get('/jobs/{identifier}')
        async def stream(identifier: str):
            async def content():
                yield b'first'
                began.set()
                await release.wait()
                yield b'last'
            return StreamingResponse(content())

        async def request() -> None:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                response = await client.get('/jobs/private-user-token?key=secret')
                assert response.text == 'firstlast'

        async with anyio.create_task_group() as group:
            group.start_soon(request)
            await began.wait()
            assert metrics.inflight['GET'] == 1 and sum(metrics.counts.values()) == 0
            release.set()
        assert metrics.inflight['GET'] == 0
        assert metrics.counts[('GET', '/jobs/{identifier}', 200, 'completed')] == 1
        output = metrics.render(tmp_path)
        assert 'private-user-token' not in output and 'secret' not in output
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            assert (await client.get('/unmatched/private-user')).status_code == 404
        assert metrics.counts[('GET', '__unmatched__', 404, 'completed')] == 1
    anyio.run(scenario)


def test_stream_cancellation_releases_inflight_and_records_499(tmp_path: Path) -> None:
    async def scenario() -> None:
        metrics = HttpMetrics()
        ready = anyio.Event()

        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'data', 'more_body': True})
            ready.set()
            await anyio.sleep_forever()

        async def receive():
            return {'type': 'http.request', 'body': b''}

        async def send(message):
            return None

        middleware = HttpMetricsMiddleware(app, metrics)
        async with anyio.create_task_group() as group:
            group.start_soon(middleware, {'type': 'http', 'method': 'POST'}, receive, send)
            await ready.wait()
            assert metrics.inflight['POST'] == 1
            group.cancel_scope.cancel()
        assert metrics.inflight['POST'] == 0
        assert metrics.counts[('POST', '__unmatched__', 499, 'cancelled')] == 1
        assert 'outcome="cancelled"' in metrics.render(tmp_path)
    anyio.run(scenario)


def test_all_store_initializers_install_metrics_before_first_claim(tmp_path: Path) -> None:
    from easel.generation_aideo_store import AideoStore
    from easel.generation_agent_store import AgentStore
    from easel.generation_character_store import CharacterStore
    from easel.generation_chat_store import ChatStore
    from easel.generation_direct_edit_store import DirectEditStore
    from easel.generation_draft_store import DraftStore
    from easel.publish_tasks import TaskStore
    from easel.publish_ledger import PublishLedger
    base = GenerationStore(tmp_path)
    for factory in (AideoStore, AgentStore, CharacterStore, ChatStore, DirectEditStore, DraftStore, VideoStore):
        factory(base)
    TaskStore(tmp_path / '_publish')
    PublishLedger(tmp_path / '_publish/ledger.sqlite3')
    expected = {'generation_jobs', 'aideo_jobs', 'agent_tasks', 'character_jobs', 'chat_jobs', 'direct_edit_jobs', 'draft_jobs', 'video_jobs', 'tasks', 'publish_runs'}
    installed = set()
    for path in (base.path, tmp_path / '_publish/tasks.sqlite3', tmp_path / '_publish/ledger.sqlite3'):
        with sqlite3.connect(path) as db:
            installed.update(row[0] for row in db.execute("SELECT tbl_name FROM sqlite_master WHERE type='trigger' AND name LIKE 'metrics_%_insert'"))
    assert installed == expected


def test_metrics_http_export_is_guarded_and_contains_real_queue_observations(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from web import app as web
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    store = GenerationStore(tmp_path)
    job = store.enqueue(ImageRequest(persona='secret-owner', request_key='secret-key', model='secret-model', prompt='secret-prompt'))
    owner = worker_owner()
    store.finish(store.claim(owner, job.id), owner, 'failed', error='secret-error')
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 1234)) as client:
        response = client.get('/metrics')
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/plain; version=0.0.4')
        assert 'easel_queue_failed_total{queue="image"} 1.0' in response.text
        assert 'secret-' not in response.text
    with TestClient(web.app, base_url='http://localhost:7860', client=('203.0.113.10', 1234)) as client:
        assert client.get('/metrics').status_code == 403
