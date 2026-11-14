"""HTTP lifecycle contracts with real SQLite and the existing-runtime hook boundary."""
from pathlib import Path
import json
import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request
from easel.generation_agent import AgentService
from easel.generation_agent_models import AgentEvent, AgentRun, AgentSnapshot, CreateTask
from easel.generation_agent_router import create_agent_router
from easel.generation_agent_store import AgentStore
from easel.generation_store import GenerationStore


class Harness:
    """External runtime fake: its durable journal survives service recreation."""
    def __init__(self) -> None:
        self.runs: list[AgentRun] = []
        self.journal: dict[str, AgentSnapshot] = {}
        self.stopped: list[str] = []
        self.deleted: list[str] = []
        self.finish = True

    async def start(self, run: AgentRun) -> None:
        self.runs.append(run)
        self.journal[run.turn_id] = AgentSnapshot(status='running')
        if self.finish:
            self.complete(run)

    def complete(self, run: AgentRun) -> None:
        self.journal[run.turn_id] = AgentSnapshot(status='completed', events=(
            AgentEvent(uuid=run.turn_id+':0', type='token', data='result'),
            AgentEvent(uuid=run.turn_id+':1', type='activity', data={'secret': 'runtime-secret', 'path': '/private/local'}),
            AgentEvent(uuid=run.turn_id+':2', type='assistant', data='final searchable result'),))

    async def poll(self, run: AgentRun) -> AgentSnapshot:
        return self.journal[run.turn_id]

    async def stop(self, run: AgentRun) -> None:
        self.stopped.append(run.turn_id)

    async def delete(self, run: AgentRun) -> None:
        self.deleted.append(run.task_id)


def setup(tmp_path: Path):
    harness = Harness()
    service = AgentService(AgentStore(GenerationStore(tmp_path)), harness)
    def authorize(request: Request, persona: str) -> str:
        owner = request.headers.get('X-Owner', 'alice')
        if persona and persona != owner:
            raise HTTPException(403, 'owner_mismatch')
        return owner
    app = FastAPI()
    app.include_router(create_agent_router(lambda: service, authorize))
    return service, harness, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', timeout=10)


def test_create_resume_search_rating_and_favorites(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, harness, client = setup(tmp_path)
        async with client:
            response = await client.post('/api/ai/agent/tasks', json={'prompt': 'first'})
            assert response.status_code == 200
            chunks = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            task_id = chunks[0]['taskId']
            assert chunks[-1]['status'] == 'completed'
            assert all(chunk['type'] != 'token' for chunk in chunks)
            base = '/api/ai/agent/tasks/' + task_id
            assert (await client.post(base+'/rating', json={'rating': 6})).status_code == 422
            assert (await client.post(base+'/rating', json={'rating': 5, 'comment': 'useful'})).status_code == 200
            assert (await client.patch(base, json={'title': 'renamed'})).status_code == 200
            assert (await client.post(base+'/favorite')).status_code == 200
            listing = (await client.get('/api/ai/agent/tasks', params={'keyword': 'searchable', 'favoriteOnly': 'true', 'pageSize': 1})).json()
            assert listing['total'] == 1 and listing['items'][0]['title'] == 'renamed'
            assert listing['items'][0]['ratingComment'] == 'useful'
            assert (await client.delete(base+'/favorite')).status_code == 200
            assert (await client.get('/api/ai/agent/tasks?favoriteOnly=true')).json()['total'] == 0
            assert (await client.post('/api/ai/agent/tasks', json={'prompt': 'second', 'taskId': task_id, 'includePartialMessages': True})).status_code == 200
            detail = (await client.get(base)).json()
            assert len(detail['messages']) == 8
            assert harness.runs[0].task_id == harness.runs[1].task_id
            assert harness.runs[0].turn_id != harness.runs[1].turn_id
            assert (await client.get(base+'/messages?lastMessageId=unknown')).json()['messages'] == []
            cursor = detail['messages'][-2]['uuid']
            assert len((await client.get(base+'/messages', params={'lastMessageId': cursor})).json()['messages']) == 1
            with service.store.store.connect() as db:
                assert db.execute('SELECT count(*) FROM agent_events').fetchone()[0] == 8
    anyio.run(scenario)


def test_share_rotation_expiry_deletion_and_owner_isolation(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, harness, client = setup(tmp_path)
        async with client:
            await client.post('/api/ai/agent/tasks', json={'prompt': 'public question'})
            task_id = harness.runs[0].task_id
            base = '/api/ai/agent/tasks/' + task_id
            for method, suffix, body in [('get', '', None), ('post', '/share', {}), ('delete', '', None), ('post', '/abort', None), ('patch', '', {'title': 'stolen'})]:
                response = await client.request(method, base+suffix, headers={'X-Owner': 'bob'}, json=body)
                assert response.status_code == 404
            assert (await client.get('/api/ai/agent/tasks', headers={'X-Owner': 'bob'})).json()['total'] == 0
            first = (await client.post(base+'/share', json={})).json()['token']
            public = await client.get('/api/ai/agent/tasks/shared/'+first)
            assert public.status_code == 200
            assert public.json()['messages'][-1]['text'] == 'final searchable result'
            assert all(secret not in public.text for secret in ('runtime-secret', '/private/local', 'userId', 'ratingComment', 'turn_id'))
            second = (await client.post(base+'/share', json={'ttlSeconds': 60})).json()['token']
            assert (await client.get('/api/ai/agent/tasks/shared/'+first)).status_code == 404
            with service.store.store.connect() as db:
                db.execute('UPDATE agent_tasks SET share_expiry=0 WHERE id=?', (task_id,))
            assert (await client.get('/api/ai/agent/tasks/shared/'+second)).status_code == 404
            third = (await client.post(base+'/share', json={})).json()['token']
            assert (await client.delete(base)).status_code == 200
            assert (await client.get(base)).status_code == 404
            assert (await client.get('/api/ai/agent/tasks/shared/'+third)).status_code == 404
            assert harness.deleted == [task_id]
    anyio.run(scenario)


def test_disconnect_recovery_and_duplicate_claim_abort(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, harness, client = setup(tmp_path)
        harness.finish = False
        run = await service.create('alice', CreateTask(prompt='keep working'))
        stream = service.stream(run, True)
        assert 'init' in await anext(stream)
        await stream.aclose()
        base = '/api/ai/agent/tasks/' + run.task_id
        async with client:
            assert (await client.post('/api/ai/agent/tasks', json={'prompt': 'duplicate', 'taskId': run.task_id})).status_code == 409
            assert (await client.post(base+'/rating', json={'rating': 3})).status_code == 409
            harness.complete(run)
            recovered = AgentService(AgentStore(GenerationStore(tmp_path)), harness)
            result = await recovered.messages('alice', run.task_id, run.turn_id+':user')
            assert result.status == 'completed' and len(result.messages) == 3
            assert len((await recovered.messages('alice', run.task_id)).messages) == 4
            next_run = await recovered.create('alice', CreateTask(prompt='continue', taskId=run.task_id))
            assert (await client.post(base+'/abort')).status_code == 200
            harness.complete(next_run)
            assert (await client.get(base)).json()['status'] == 'aborted'
            assert (await client.post(base+'/abort')).status_code == 409
            assert harness.stopped == [next_run.turn_id]
    anyio.run(scenario)


def test_multimodal_blocks_survive_real_route(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, harness, client = setup(tmp_path)
        prompt = [{'type': 'image', 'source': {'type': 'url', 'url': 'https://example.com/image.png'}},
                  {'type': 'video', 'source': {'type': 'url', 'url': 'https://example.com/video.mp4'}},
                  {'type': 'document', 'source': {'type': 'text', 'media_type': 'text/plain', 'data': 'document'}},
                  {'type': 'document', 'source': {'type': 'content', 'content': [{'type': 'text', 'text': 'embedded'}]}}]
        async with client:
            assert (await client.post('/api/ai/agent/tasks', json={'prompt': prompt})).status_code == 200
            assert harness.runs[0].model_dump(mode='json', exclude_none=True)['prompt'] == prompt
            assert (await client.post('/api/ai/agent/tasks', json={'prompt': [{'type': 'image', 'source': {'type': 'url', 'url': 'file:///etc/passwd'}}]})).status_code == 422
    anyio.run(scenario)


def test_atomic_claim_and_stale_turn_cannot_overwrite_resume(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, harness, client = setup(tmp_path)
        async with client:
            await client.post('/api/ai/agent/tasks', json={'prompt': 'first'})
            original = harness.runs[0]
            harness.finish = False
            statuses: list[int] = []
            async def claim() -> None:
                from easel.generation_models import GenerationError
                try:
                    await service.create('alice', CreateTask(prompt='resume', taskId=original.task_id))
                    statuses.append(200)
                except GenerationError as error:
                    statuses.append(error.status)
            async with anyio.create_task_group() as group:
                group.start_soon(claim)
                group.start_soon(claim)
            assert sorted(statuses) == [200, 409]
            service.store.ingest(original, AgentSnapshot(status='error', events=(AgentEvent(uuid='stale', type='assistant', data='wrong'),)))
            detail = (await client.get('/api/ai/agent/tasks/'+original.task_id)).json()
            assert detail['status'] == 'running'
            assert all(event['uuid'] != 'stale' for event in detail['messages'])
    anyio.run(scenario)


def test_inline_media_uri_bounds_and_persistence(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, harness, client = setup(tmp_path)
        prompt = [
            {'type': 'image', 'source': {'type': 'url', 'url': 'data:image/png;base64,aW1hZ2U='}},
            {'type': 'video', 'source': {'type': 'url', 'url': 'data:video/mp4;base64,dmlkZW8='}},
            {'type': 'document', 'source': {'type': 'url', 'url': 'data:application/pdf;base64,cGRm'}},
        ]
        invalid = (
            'file:///etc/passwd', 'ftp://example.com/media',
            'data:text/html;base64,PHNjcmlwdD4=', 'data:image/png,plain',
            'data:image/png;base64,', 'data:image/png;base64,abc',
            'data:image/png;base64,!!!!', 'data:image/png;base64,YQ===',
            'data:image/png;base64,YQ==\n',
            'data:image/png;base64,' + 'AAAA' * (4 * 1024 * 1024),
        )
        async with client:
            response = await client.post('/api/ai/agent/tasks', json={'prompt': prompt})
            assert response.status_code == 200
            task_id = harness.runs[0].task_id
            detail = (await client.get('/api/ai/agent/tasks/' + task_id)).json()
            assert [block['source'] for block in detail['messages'][0]['data']] == [block['source'] for block in prompt]
            assert harness.runs[0].model_dump(mode='json', exclude_none=True)['prompt'] == prompt
            for url in invalid:
                response = await client.post('/api/ai/agent/tasks', json={
                    'prompt': [{'type': 'image', 'source': {'type': 'url', 'url': url}}]})
                assert response.status_code == 422
            assert len(harness.runs) == 1
    anyio.run(scenario)
