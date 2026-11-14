"""Task lifecycle, partial failures and owned material projection contracts."""
from pathlib import Path
import anyio
from easel.content_library import ContentLibrary
from easel.content_library_models import ContentScope, ContentFilter, UsageInput, GroupInput
from easel.generation_draft import DraftGeneration
from easel.generation_draft_projection import project_draft
from tests.test_generation_draft_support import setup

BASE = '/api/ai/draft-generation'


def test_batch_partial_failure_idempotency_and_usage_safe_projection(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, runtime, client = setup(tmp_path)
        payload = {'quantity': 2, 'prompt': 'pottery', 'imageModel': 'local-image', 'imageCount': 2}
        async with client:
            response = await client.post(BASE+'/image-text', json=payload, headers={'Idempotency-Key': 'batch'})
            assert response.status_code == 200
            ids = response.json()['taskIds']
            assert len(ids) == 2
            assert (await client.post(BASE+'/image-text', json=payload, headers={'Idempotency-Key': 'batch'})).json()['taskIds'] == ids
            assert (await client.post(BASE+'/image-text', json={**payload, 'quantity': 1}, headers={'Idempotency-Key': 'batch'})).status_code == 409
            assert (await client.get(BASE+'/stats')).json()['generatingCount'] == 2
            runtime.fail.add(ids[0])
            for identifier in ids:
                await service.execute(identifier)
            jobs = (await client.post(BASE+'/query', json={'taskIds': ids})).json()
            assert [job['status'] for job in jobs] == ['failed', 'success']
            assert len(jobs[0]['media']) == 1
            assert (await client.get(BASE+'/stats')).json()['generatingCount'] == 0
            assert (await client.get(BASE, params={'pageSize': 1})).json()['total'] == 2
            result = jobs[1]['response']
            assert result['title'] == 'Morning pottery workshop' and len(result['imageUrls']) == 2
            library = ContentLibrary(tmp_path)
            scope = ContentScope(persona='alice', domain='material')
            material = library.get(scope, result['materialId'])
            assert material.use_count == 0 and material.auto_delete_media is False
            assert material.options['source'] == 'draft-generation' and len(material.media) == 2
            library.increment_usage(scope, UsageInput(ids=(material.id,), run_id='publish-1'))
            project_draft(tmp_path, service.store.get('alice', ids[1]))
            assert library.get(scope, material.id).use_count == 1
            assert library.list_records(scope, ContentFilter()).total == 1
            assert await service.execute(ids[1]) is None
    anyio.run(scenario)


def test_internal_aliases_bind_owner_and_media_only_preserves_caption_bypass(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, runtime, client = setup(tmp_path)
        async with client:
            payload = {'userId': 'alice', 'userType': 'system', 'model': 'native-video', 'prompt': 'wheel turning', 'draftType': 'video', 'captionPrompt': 'ignored'}
            result = await client.post('/api/internal/ai/draft-generation/v2', json=payload)
            assert result.status_code == 200
            identifier = result.json()['taskIds'][0]
            await service.execute(identifier)
            result = await client.post('/api/internal/ai/draft-generation/task', json={'userId': 'alice', 'taskId': identifier})
            assert result.json()['response']['materialId'] == ''
            assert len(result.json()['response']['mediaIds']) == 1
            assert runtime.plan_memories == []
            assert (await client.post('/api/internal/ai/draft-generation/task', json={'userId': 'alice', 'taskId': identifier}, headers={'X-Owner': 'bob'})).status_code == 403
            assert (await client.get(BASE+'/'+identifier, headers={'X-Owner': 'bob'})).status_code == 404
            assert (await client.post(BASE+'/query', json={'taskIds': [identifier]}, headers={'X-Owner': 'bob'})).status_code == 404
            image = await client.post('/api/internal/ai/draft-generation/image-text', json={'userId': 'alice', 'imageModel': 'local-image', 'prompt': 'clay cup', 'imageCount': 1})
            assert image.status_code == 200
            assert (await client.post(BASE+'/v2', json={'model': 'native-video', 'quantity': 11})).status_code == 422
    anyio.run(scenario)


def test_memory_sampling_deletion_owner_and_disabled_default(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, runtime, client = setup(tmp_path)
        async with client:
            empty = await client.post(BASE+'/memory/regenerate', json={'contentType': 'image-text'})
            assert empty.json()['sampleCount'] == 0 and runtime.memories == []
            made = await client.post(BASE+'/image-text', json={'imageModel': 'local-image', 'prompt': 'workshop', 'imageCount': 1})
            await service.execute(made.json()['taskIds'][0])
            generated = await client.post(BASE+'/memory/regenerate', json={'contentType': 'image-text', 'plannerModel': 'planner'})
            memory = generated.json()
            assert memory['sampleCount'] == 2 and len(memory['items']) == 1
            assert runtime.memories[0].model == 'planner'
            assert (await client.get(BASE+'/memory', headers={'X-Owner': 'bob'})).json() == []
            item = memory['items'][0]['id']
            assert (await client.delete(BASE+'/memory/items/'+item, headers={'X-Owner': 'bob'})).status_code == 404
            for disabled in (True, False):
                made = await client.post(BASE+'/image-text', json={'imageModel': 'local-image', 'prompt': 'workshop', 'imageCount': 1, 'disableMemory': disabled})
                await service.execute(made.json()['taskIds'][0])
            assert runtime.plan_memories[-2:] == [(), ('Prefer short workshop captions.',)]
            assert (await client.delete(BASE+'/memory/items/'+item)).json()['items'] == []
    anyio.run(scenario)


def test_recovery_checkpoints_and_url_sync_result(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, runtime, client = setup(tmp_path)
        async with client:
            made = await client.post(BASE+'/image-text', json={'imageModel': 'local-image', 'prompt': 'cup', 'imageCount': 2})
            identifier = made.json()['taskIds'][0]
            job = service.store.claim('99999999:dead', identifier)
            assert job is not None
            plan = await runtime.plan(job, ())
            job = service.store.save(job.model_copy(update={'plan': plan}), '99999999:dead')
            media = await runtime.media(job, 0)
            service.store.save(job.model_copy(update={'media': (media,)}), '99999999:dead')
            recovered = DraftGeneration(tmp_path, runtime)
            assert recovered.store.recover() == 1
            assert (await recovered.execute(identifier)).state == 'succeeded'
            assert runtime.calls.count((identifier, 0)) == 1
            assert runtime.calls.count((identifier, 1)) == 1
            url = await client.post(BASE+'/from-video-url', json={'videoUrl': 'https://example.com/clip.mp4'})
            assert url.status_code == 200 and url.json()['materialId']
            assert (await client.post(BASE+'/from-video-url', json={'videoUrl': 'http://127.0.0.1/secret.mp4'})).status_code == 400
    anyio.run(scenario)


def test_group_ownership_atomic_claim_and_stale_checkpoint(tmp_path: Path) -> None:
    async def scenario() -> None:
        from easel.generation_models import GenerationError
        import pytest
        service, runtime, client = setup(tmp_path)
        group = ContentLibrary(tmp_path).create_group(ContentScope(persona='bob', domain='material'), GroupInput(name='private'))
        async with client:
            rejected = await client.post(BASE+'/image-text', json={'imageModel': 'local-image', 'prompt': 'cup', 'groupId': group.id})
            assert rejected.status_code == 404 and runtime.calls == []
            made = await client.post(BASE+'/image-text', json={'imageModel': 'local-image', 'prompt': 'cup', 'imageCount': 1})
            identifier = made.json()['taskIds'][0]
            job = service.store.claim('99999999:old', identifier)
            assert job is not None and service.store.claim('99999998:other', identifier) is None
            assert service.store.recover() == 1
            replacement = service.store.claim('99999998:new', identifier)
            assert replacement is not None
            with pytest.raises(GenerationError, match='draft_worker_stale'):
                service.store.save(job.model_copy(update={'state': 'succeeded'}), '99999999:old')
            assert service.store.get('alice', identifier).state == 'running'
    anyio.run(scenario)
