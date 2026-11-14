"""Pinned automatic-memory and platform-selection business contracts."""
from pathlib import Path
import time
import anyio
from easel.content_library import ContentLibrary
from easel.content_library_models import ContentScope
from easel.generation_draft_models import DraftJob, DraftPlan, ImageDraftInput, VideoDraftInput
from easel.generation_draft_platforms import compatible_platforms
from easel.generation_draft_scheduler import refresh_due
from tests.test_generation_draft_support import setup


def test_source_platform_limits_and_utf16_lengths() -> None:
    image = DraftJob(id='image', persona='alice', request_key='image', createdAt=1, updatedAt=1,
        request=ImageDraftInput(prompt='clay', imageModel='local-image', imageCount=5),
        plan=DraftPlan(title='X'*31, description='X'*1101, topics=('pottery',)))
    assert compatible_platforms(image) == ('tiktok', 'instagram', 'facebook', 'pinterest', 'wxGzh', 'linkedin')
    video = DraftJob(id='video', persona='alice', request_key='video', createdAt=1, updatedAt=1,
        request=VideoDraftInput(prompt='clay', model='local-video', duration=4, aspectRatio='16:9'),
        plan=DraftPlan(title='Clay', description='Shape a cup.', topics=('pottery',), videoPrompt='Clay'))
    assert compatible_platforms(video) == ('tiktok', 'douyin', 'bilibili', 'youtube', 'facebook', 'threads', 'pinterest', 'xhs', 'wxSph', 'linkedin')
    emoji = video.model_copy(update={'plan': video.plan.model_copy(update={'title': '😀'*16})})
    assert 'douyin' not in compatible_platforms(emoji)


def test_projection_normalizes_source_aliases_and_preserves_explicit_selection(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, _, client = setup(tmp_path)
        async with client:
            for platforms in (None, ['xhs', 'wxSph', 'KWAI', 'wxGzh', 'douyin-open'], []):
                request = {'prompt': 'clay', 'imageModel': 'local-image', 'imageCount': 1}
                if platforms is not None:
                    request['platforms'] = platforms
                result = await client.post('/api/ai/draft-generation/image-text', json=request)
                identifier = result.json()['taskIds'][0]
                job = await service.execute(identifier)
                record = ContentLibrary(tmp_path).get(ContentScope(persona='alice', domain='material'), job.response.materialId)
                if platforms is None:
                    assert 'xiaohongshu' in record.account_types and 'wechat-oa' in record.account_types
                    assert 'youtube' not in record.account_types
                else:
                    expected = ('xiaohongshu', 'weixin-channels', 'kuaishou', 'wechat-oa', 'douyin-open') if platforms else ()
                    assert record.account_types == expected
                    assert record.options['generationParams']['platforms'] == platforms
    anyio.run(scenario)


def test_hourly_memory_refresh_recent_users_and_durable_duplicate_claim(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        service, runtime, client = setup(tmp_path)
        async with client:
            for persona in ('alice', 'old-user'):
                result = await client.post('/api/ai/draft-generation/image-text', headers={'X-Owner': persona},
                    json={'prompt': 'clay', 'imageModel': 'local-image', 'imageCount': 1})
                await service.execute(result.json()['taskIds'][0])
            with service.store.base.connect() as db:
                db.execute("UPDATE draft_jobs SET updated_at=1 WHERE persona='old-user'")
            library = ContentLibrary(tmp_path)
            with library.connect() as db:
                db.execute("UPDATE content_records SET body=json_set(body,'$.updated_at','1970-01-01T00:00:01+00:00') WHERE persona='old-user'")
            assert await refresh_due(service.memory) == 2
            assert await refresh_due(service.memory) == 0
            assert len(runtime.memories) == 1
            assert service.memory.list('old-user') == ()
            assert {memory.contentType for memory in service.memory.list('alice')} == {'video', 'image-text'}
            with service.store.base.connect() as db:
                row = db.execute('SELECT completed,failures FROM draft_memory_refresh').fetchone()
                assert tuple(row) == (1, 0)
            next_hour = time.time() + 3600
            monkeypatch.setattr('easel.generation_draft_scheduler.time.time', lambda: next_hour)
            assert await refresh_due(service.memory) == 2
            assert len(runtime.memories) == 2
    anyio.run(scenario)
