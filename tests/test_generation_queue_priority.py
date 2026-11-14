"""Scheduling regressions across real SQLite stores and concurrent execution."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import sqlite3
import anyio
import pytest
from easel.generation_app_config import save_config, revision
from easel.generation_config_models import AppConfig, ConfigUpdate
from easel.generation_draft import DraftGeneration, _dispatch_draft_jobs
from easel.generation_draft_models import ImageDraftInput
from easel.generation_draft_store import DraftStore
from easel.generation_images import ImageGeneration
from easel.generation_models import GenerationError, GenerationJob, ImageRequest, ImageResult
from easel.generation_store import GenerationStore
from easel.publish_recovery import worker_owner
from tests.test_generation_draft_support import Runtime


def configure(outputs: Path, **queue: int) -> None:
    config = AppConfig.model_validate({'queue': queue, 'models': {'image': {'generation': [
        {'name': 'low', 'queuePriority': 1000}, {'name': 'high', 'queuePriority': 2},
        {'name': 'urgent', 'queuePriority': 1}]}}})
    save_config(outputs, ConfigUpdate(config=config, revision=revision(outputs)))


def draft(store: DraftStore, key: str, model: str) -> str:
    return store.enqueue('alice', ImageDraftInput(imageModel=model, prompt='clay', imageCount=1), key).taskIds[0]


def test_priority_isolation_and_hot_limits_preserve_active_owners(tmp_path: Path) -> None:
    configure(tmp_path, draftConcurrency=1, lowPriorityConcurrency=1)
    store = DraftStore(GenerationStore(tmp_path))
    low1, low2 = draft(store, 'l1', 'low'), draft(store, 'l2', 'low')
    owner = worker_owner()
    low = store.claim(owner)
    assert low.id == low1
    high = draft(store, 'h', 'high')
    urgent = draft(store, 'u', 'urgent')
    assert store.claim(owner).id == urgent
    assert store.claim(owner, low2) is None
    assert store.claim(owner, high) is None
    from easel.http_metrics import HttpMetrics
    exported = HttpMetrics().render(tmp_path)
    assert 'easel_queue_pool_jobs{queue="draft",pool="low",state="running"} 1' in exported
    assert 'easel_queue_pool_jobs{queue="draft",pool="low",state="queued"} 1' in exported
    assert 'easel_queue_concurrency_limit{queue="draft",pool="normal"} 1' in exported
    configure(tmp_path, draftConcurrency=2, lowPriorityConcurrency=1)
    assert store.claim(owner, high).id == high
    configure(tmp_path, draftConcurrency=1, lowPriorityConcurrency=1)
    assert store.claim(owner) is None
    assert store.recover() == 0
    with pytest.raises(GenerationError, match='draft_worker_stale'):
        store.save(low.model_copy(update={'state': 'succeeded'}), 'other-owner')
    store.save(low.model_copy(update={'state': 'succeeded'}), owner)
    assert store.claim(owner).id == low2


def _claim_process(outputs: str) -> str:
    result = GenerationStore(Path(outputs)).claim(worker_owner())
    return result.id if result else ''


def test_independent_processes_cannot_exceed_global_image_limit(tmp_path: Path) -> None:
    configure(tmp_path, imageConcurrency=2)
    store = GenerationStore(tmp_path)
    for index in range(8):
        store.enqueue(ImageRequest(persona='alice', request_key=str(index), model='high', prompt='clay'))
    with ProcessPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_claim_process, [str(tmp_path)] * 8))
    claimed = [item for item in results if item]
    assert len(claimed) == len(set(claimed)) == 2
    assert store.recover() == 2
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM generation_jobs WHERE state='unknown'").fetchone()[0] == 2


def test_legacy_image_database_migrates_without_inventing_history(tmp_path: Path) -> None:
    path = tmp_path / '_generation/jobs.sqlite3'
    path.parent.mkdir()
    request = ImageRequest(persona='alice', request_key='old', prompt='clay', model='old')
    job = GenerationJob(id='old', persona='alice', state='queued', request=request, created_at=1, updated_at=1)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE generation_jobs(id TEXT PRIMARY KEY,persona TEXT,request_key TEXT,digest TEXT,state TEXT,owner TEXT,body TEXT)')
        db.execute('INSERT INTO generation_jobs VALUES(?,?,?,?,?,?,?)', ('old', 'alice', 'old', 'digest', 'queued', '', job.model_dump_json()))
    store = GenerationStore(tmp_path)
    with store.connect() as db:
        row = db.execute('SELECT priority,queue_class FROM generation_jobs').fetchone()
        assert tuple(row) == (0, 'normal')
        assert db.execute('SELECT count(*) FROM operational_observations').fetchone()[0] == 0
    assert store.claim(worker_owner()).id == 'old'


def test_draft_background_pool_runs_high_while_low_is_busy(tmp_path: Path) -> None:
    async def scenario() -> None:
        configure(tmp_path, draftConcurrency=2, lowPriorityConcurrency=1)
        reached: set[str] = set()
        release = anyio.Event()

        class PausedRuntime(Runtime):
            async def media(self, job, position):
                reached.add(job.id)
                await release.wait()
                return await super().media(job, position)

        runtime = PausedRuntime(tmp_path)
        service = DraftGeneration(tmp_path, runtime)
        def submit(key: str, model: str) -> str:
            return service.submit('alice', ImageDraftInput(imageModel=model, prompt='clay', imageCount=1), key).taskIds[0]

        low1 = submit('l1', 'low')
        low2 = submit('l2', 'low')
        async with anyio.create_task_group() as group:
            group.start_soon(_dispatch_draft_jobs, lambda: service)
            with anyio.fail_after(5):
                while low1 not in reached:
                    await anyio.sleep(0.01)
            high = [submit(str(i), 'high') for i in range(2)]
            with anyio.fail_after(5):
                while not set(high).issubset(reached):
                    await anyio.sleep(0.01)
            assert low2 not in reached
            assert len(reached) == 3
            release.set()
            with anyio.fail_after(5):
                while any(service.store.get('alice', key).state != 'succeeded' for key in (*high, low1, low2)):
                    await anyio.sleep(0.02)
            group.cancel_scope.cancel()
        assert len(runtime.calls) == 4
    anyio.run(scenario)


def test_cancelled_image_is_unknown_and_cannot_be_resubmitted(tmp_path: Path) -> None:
    async def scenario() -> None:
        started = anyio.Event()
        calls = 0

        class PausedImage(ImageGeneration):
            async def _generate(self, job, owner) -> ImageResult:
                nonlocal calls
                calls += 1
                started.set()
                await anyio.sleep_forever()

        service = PausedImage(tmp_path, tmp_path, {})
        job = service.store.enqueue(ImageRequest(persona='alice', request_key='one', model='high', prompt='clay'))
        async with anyio.create_task_group() as group:
            group.start_soon(service.execute, job.id)
            await started.wait()
            group.start_soon(service.execute, job.id)
            group.cancel_scope.cancel()
        assert service.store.get('alice', job.id).state == 'unknown'
        assert service.store.recover() == 0
        assert await service.execute(job.id) is None
        assert calls == 1
    anyio.run(scenario)
