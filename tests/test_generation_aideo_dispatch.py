"""Durable Aideo recovery through real local provider HTTP and owned media."""
import anyio
import pytest
import json
import subprocess
import sys
import threading
from urllib.parse import parse_qs
import httpx
from easel.generation_aideo_dispatch import dispatch_aideo, reconcile_pending
from easel.generation_aideo_service import AideoGeneration
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.ownership import OwnershipError, owned_media_path
from tests.test_generation_aideo import aideo


@pytest.fixture(autouse=True)
def provider_transcript(aideo):
    """Retain the local HTTP witness alongside pytest's owned output directory."""
    yield
    _, outputs, records, _, _, _ = aideo
    (outputs.parent / 'provider-transcript.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))


@pytest.mark.parametrize('workflow', ['aideo', 'style-transfer', 'drama-recap'])
@pytest.mark.parametrize('lost_receipt', [False, True])
def test_historical_paid_intent_replays_without_credentials(aideo, workflow, lost_receipt):
    client, outputs, records, behavior, factory, base = aideo
    payload = {'persona': 'alice', 'request_key': 'history'}
    payload.update({'prompt': 'edit', 'multiInputs': ['vid://source']} if workflow == 'aideo' else
                   {'videoInput': 'vid://source', 'resolution': '720p'} if workflow == 'style-transfer' else
                   {'vids': ['vid://source']})
    route = '/api/ai/aideo/tasks' if workflow == 'aideo' else f'/api/ai/aideo/{workflow}/tasks'
    behavior['drop'] = lost_receipt
    original = client.post(route, json=payload).json()
    original_job = factory().store.get('alice', original['taskId'])
    settings = factory().settings
    settings.clear()

    replay = client.post(route, json=payload)

    assert replay.status_code == 200
    assert replay.json()['taskId'] == original['taskId']
    assert factory().store.get('alice', original['taskId']).state == original_job.state
    changed = {**payload, 'prompt': 'changed'} if workflow == 'aideo' else {
        **payload, **({'style': 'changed'} if workflow == 'style-transfer' else {'recapStyle': 'changed'})}
    assert client.post(route, json=changed).status_code == 409
    assert len([row for row in records if row[0] in ('Submit', 'AsyncVCreativeTask', 'CreateDramaRecapTask')]) == 1


@pytest.mark.parametrize('workflow,response', [
    ('aideo', {'Status': 'Completed', 'ApiResponses': [{'VodTaskType': 'Highlight', 'Highlight': {'Edits': [{'Vid': 'output'}]}}]}),
    ('style-transfer', {'Status': 'success', 'OutputJson': '{"vid":"output"}'}),
    ('drama-recap', {'TaskId': 'recap-123', 'Status': 'Completed', 'Vid': 'output'}),
])
def test_background_restart_recovers_dead_claims_across_owners_without_replaying_unknown(aideo, workflow, response):
    client, outputs, records, behavior, factory, base = aideo
    route = '/api/ai/aideo/tasks' if workflow == 'aideo' else f'/api/ai/aideo/{workflow}/tasks'
    request = {'prompt': 'edit', 'multiInputs': ['vid://source']} if workflow == 'aideo' else {
        'videoInput': 'vid://source', 'resolution': '720p'} if workflow == 'style-transfer' else {'vids': ['vid://source']}
    identifiers = {owner: client.post(route, json={**request, 'persona': owner, 'request_key': 'recover'}).json()['taskId']
                   for owner in ('alice', 'bob')}
    store = factory().store
    store.save(store.get('bob', identifiers['bob']).model_copy(update={'state': 'unknown'}))
    behavior['drop'] = True
    unknown = client.post(route, json={**request, 'persona': 'alice', 'request_key': 'ambiguous'}).json()['taskId']
    behavior.update(drop=False, response=response)
    # A real process exits with the durable claim held, like a server crash.
    crashed = subprocess.run([sys.executable, '-c', '''
import os, sys
from pathlib import Path
from easel.generation_aideo_service import AideoGeneration
with AideoGeneration(Path(sys.argv[1]), {}).store.poll('alice', sys.argv[2]) as claim:
    assert claim.owner
    print(claim.owner, flush=True)
    os._exit(0)
''', str(outputs), identifiers['alice']], capture_output=True, text=True, check=True)
    assert crashed.returncode == 0
    stale_job = store.get('alice', identifiers['alice'])

    anyio.run(reconcile_pending, factory())

    for owner, identifier in identifiers.items():
        job = factory().store.get(owner, identifier)
        assert job.state == 'succeeded'
        asset, = GenerationStore(outputs).assets(owner).items
        assert owned_media_path(outputs, owner, asset.path)[1].read_bytes() == (outputs / 'project/clip.mp4').read_bytes()
        with pytest.raises(OwnershipError):
            owned_media_path(outputs, 'bob' if owner == 'alice' else 'alice', asset.path)
    assert factory().store.get('alice', unknown).state == 'unknown'
    assert factory().store.get('alice', unknown).provider_id == ''
    with pytest.raises(GenerationError, match='aideo_poll_worker_stale'):
        store.save(stale_job.model_copy(update={'state': 'failed'}), crashed.stdout.strip())
    assert len([row for row in records if row[0] in ('Submit', 'AsyncVCreativeTask', 'CreateDramaRecapTask')]) == 3
    assert len([row for row in records if row[0] in ('GetAideoTaskResult', 'GetVCreativeTaskResult', 'QueryDramaRecapTask')]) == 2


@pytest.mark.parametrize('workflow,response', [
    ('aideo', {'Status': 'Completed', 'ApiResponses': [{'VodTaskType': 'Highlight', 'Highlight': {'Edits': [{'Vid': 'output'}]}}]}),
    ('style-transfer', {'Status': 'success', 'OutputJson': '{"vid":"output"}'}),
    ('drama-recap', {'TaskId': 'recap-123', 'Status': 'Completed', 'Vid': 'output'}),
])
def test_competing_dispatch_and_http_get_do_not_poll_or_archive_a_live_claim(aideo, monkeypatch, workflow, response):
    import easel.generation_aideo_media as media
    client, outputs, records, behavior, factory, base = aideo
    route = '/api/ai/aideo/tasks' if workflow == 'aideo' else f'/api/ai/aideo/{workflow}/tasks'
    payload = {'persona': 'alice', 'request_key': 'concurrent'}
    payload.update({'prompt': 'edit', 'multiInputs': ['vid://source']} if workflow == 'aideo' else
                   {'videoInput': 'vid://source', 'resolution': '720p'} if workflow == 'style-transfer' else {'vids': ['vid://source']})
    identifier = client.post(route, json=payload).json()['taskId']
    behavior['response'] = response
    downloading, release = threading.Event(), threading.Event()
    download = media.download_to_file

    def blocked_download(*args, **kwargs):
        downloading.set()
        assert release.wait(10), 'competing callers did not finish'
        return download(*args, **kwargs)

    monkeypatch.setattr(media, 'download_to_file', blocked_download)

    async def race():
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(reconcile_pending, factory())
            try:
                assert await anyio.to_thread.run_sync(downloading.wait, 10), 'archive did not begin'
                await reconcile_pending(factory())
                await anyio.to_thread.run_sync(lambda: subprocess.run([sys.executable, '-c', '''
import anyio, json, sys
from pathlib import Path
from easel.generation_aideo_dispatch import reconcile_pending
from easel.generation_aideo_service import AideoGeneration
anyio.run(reconcile_pending, AideoGeneration(Path(sys.argv[1]), json.loads(sys.argv[2])))
''', str(outputs), json.dumps(dict(factory().settings))], capture_output=True, check=True, timeout=10))
                result = await anyio.to_thread.run_sync(lambda: client.get(f'{route}/{identifier}?persona=alice'))
                assert result.json()['state'] == 'running'
            finally:
                release.set()
        await reconcile_pending(factory())

    anyio.run(race)

    assert factory().store.get('alice', identifier).state == 'succeeded'
    assert len(GenerationStore(outputs).assets('alice').items) == 1
    assert len([row for row in records if row[0] in ('GetAideoTaskResult', 'GetVCreativeTaskResult', 'QueryDramaRecapTask')]) == 1
    assert len([row for row in records if row[0] == 'media']) == 1
    assert len([row for row in records if row[0] in ('Submit', 'AsyncVCreativeTask', 'CreateDramaRecapTask')]) == 1


def test_background_loop_resumes_after_credentials_return_and_bounds_provider_concurrency(aideo, monkeypatch):
    import easel.generation_aideo_dispatch as dispatch
    client, outputs, records, behavior, factory, base = aideo
    identifiers = [client.post('/api/ai/aideo/tasks', json={'persona': 'alice', 'request_key': str(index),
        'prompt': 'describe', 'multiInputs': ['vid://source']}).json()['taskId'] for index in range(5)]
    settings = factory().settings
    saved_settings = dict(settings)
    settings.clear()
    anyio.run(reconcile_pending, factory())
    assert [factory().store.get('alice', identifier).state for identifier in identifiers] == ['running'] * 5
    assert not any(row[0] == 'GetAideoTaskResult' for row in records)
    settings.update(saved_settings)
    behavior['response'] = {'Status': 'Completed', 'ApiResponses': [{'VodTaskType': 'Vision', 'Vision': {'Description': 'blue'}}]}
    original_send = httpx.AsyncClient.send
    active = maximum = completed = 0
    monkeypatch.setattr(dispatch, 'POLL_INTERVAL', 0)

    async def scenario():
        entered, release, done = anyio.Event(), anyio.Event(), anyio.Event()

        async def limited_send(self, request, **kwargs):
            nonlocal active, maximum
            if parse_qs(request.url.query.decode()).get('Action') != ['GetAideoTaskResult']:
                return await original_send(self, request, **kwargs)
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                entered.set()
            try:
                await release.wait()
                return await original_send(self, request, **kwargs)
            finally:
                active -= 1

        class ObservedGeneration(AideoGeneration):
            async def get(self, persona: str, identifier: str):
                nonlocal completed
                result = await super().get(persona, identifier)
                if result.state == 'succeeded':
                    completed += 1
                    if completed == 5:
                        done.set()
                return result

        monkeypatch.setattr(httpx.AsyncClient, 'send', limited_send)
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(dispatch_aideo, lambda: ObservedGeneration(outputs, settings))
                await entered.wait()
                release.set()
                await done.wait()
                tasks.cancel_scope.cancel()

    anyio.run(scenario)

    assert maximum == 4
    assert [factory().store.get('alice', identifier).state for identifier in identifiers] == ['succeeded'] * 5
    assert len([row for row in records if row[0] == 'Submit']) == 5
