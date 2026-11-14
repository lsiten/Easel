"""Competing process, dispatcher and direct execution share one native paid POST."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import os
import anyio
import httpx
from easel.generation_images import ImageGeneration, dispatch_images
from easel.generation_models import ImageRequest
from tests.generation_support import generation as generation
from web import app as web


def execute_process(arguments: tuple[str, str, str]) -> None:
    outputs, project, identifier = arguments
    service = ImageGeneration(Path(outputs), Path(project), dict(os.environ))
    anyio.run(service.execute, identifier)


def test_native_post_is_unique_across_process_dispatch_and_direct_route_execution(generation) -> None:
    outputs, records, _ = generation
    service = web._image_generation()
    request = ImageRequest(persona='alice', request_key='competing', model='provider-image', prompt='clay cup')
    job = service.submit(request)
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute_process, (str(outputs), str(web.PROJECT_ROOT), job.id)) for _ in range(2)]

        async def scenario() -> None:
            async with anyio.create_task_group() as group:
                group.start_soon(dispatch_images, web._image_generation)
                for _ in range(6):
                    group.start_soon(service.execute, job.id)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(web.app, client=('127.0.0.1', 1234)), base_url='http://localhost:7860') as client:
                    response = await client.post('/api/ai/image/generate', json=request.model_dump(mode='json'))
                    assert response.status_code == 200
                    assert response.json()['id'] == job.id
                with anyio.fail_after(20):
                    while service.store.get('alice', job.id).state in ('queued', 'running'):
                        await anyio.sleep(0.02)
                group.cancel_scope.cancel()
        anyio.run(scenario)
        for future in futures:
            future.result(timeout=20)
    assert service.store.get('alice', job.id).state == 'succeeded'
    assert sum(path == '/v1/images/generations' for path, _, _ in records) == 1
    assert len(service.store.assets('alice').items) == 1
