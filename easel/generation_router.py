"""Image generation, task history, and generated assets on the existing web app."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import anyio
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from easel.generation_images import ImageGeneration
from easel.generation_catalog import image_models
from easel.generation_models import GenerationError, GenerationJob, GenerationState, GenerationStatus, ImageEditRequest, ImageRequest
from easel.ownership import OwnershipError


@contextmanager
def generation_errors() -> Iterator[None]:
    try:
        yield
    except GenerationError as error:
        raise HTTPException(error.status, error.code) from error
    except OwnershipError as error:
        raise HTTPException(403, 'generation_input_ownership_invalid') from error
    except ValidationError as error:
        raise HTTPException(409, 'generation_model_options_invalid') from error


def create_router(factory: Callable[[], ImageGeneration], profile_valid: Callable[[str], bool],
                  require_access: Callable[[Request], None]) -> APIRouter:
    router = APIRouter(prefix='/api/ai', tags=['creation'])

    def owner(persona: str, request: Request) -> None:
        if request.method != 'GET' or not getattr(request.state, 'caller_key', None):
            require_access(request)
        if not profile_valid(persona):
            raise HTTPException(400, 'persona_invalid')

    async def submit(payload: ImageRequest, request: Request, asynchronous: bool) -> GenerationJob:
        owner(payload.persona, request)
        with generation_errors():
            service = factory()
            job = service.submit(payload, str(request.base_url))
            if asynchronous or job.state not in ('queued', 'running'):
                return job
            await service.execute(job.id)
            with anyio.move_on_after(910):
                while True:
                    result = service.store.get(job.persona, job.id)
                    if result.state not in ('queued', 'running'):
                        return result
                    await anyio.sleep(0.1)
            raise HTTPException(504, {'code': 'generation_still_running', 'job_id': job.id})

    @router.post('/image/generate')
    async def generate(payload: ImageRequest, request: Request):
        if payload.operation != 'generate':
            raise HTTPException(400, 'generation_operation_invalid')
        return await submit(payload, request, False)

    @router.post('/image/generate/async')
    async def generate_async(payload: ImageRequest, request: Request):
        if payload.operation != 'generate':
            raise HTTPException(400, 'generation_operation_invalid')
        return await submit(payload, request, True)

    @router.post('/image/edit')
    async def edit(payload: ImageEditRequest, request: Request):
        if payload.operation != 'edit':
            raise HTTPException(400, 'generation_operation_invalid')
        return await submit(payload, request, False)

    @router.post('/image/edit/async')
    async def edit_async(payload: ImageEditRequest, request: Request):
        if payload.operation != 'edit':
            raise HTTPException(400, 'generation_operation_invalid')
        return await submit(payload, request, True)

    @router.get('/image/task/{identifier}')
    def task(identifier: str, persona: str, request: Request):
        owner(persona, request)
        with generation_errors():
            return factory().store.get(persona, identifier)

    @router.get('/models/image/generation')
    def generation_models(persona: str, request: Request):
        owner(persona, request)
        with generation_errors():
            service = factory()
            return image_models(service.store.outputs, persona, service.settings, 'generate')

    @router.get('/models/image/edit')
    def edit_models(persona: str, request: Request):
        owner(persona, request)
        with generation_errors():
            service = factory()
            return image_models(service.store.outputs, persona, service.settings, 'edit')

    @router.get('/logs')
    def logs(persona: str, request: Request, page: int = 1, page_size: int = 30,
             state: GenerationState | None = None, model: str = '', status: GenerationStatus | None = None,
             kind: str = Query(default='', alias='type'), channel: str = ''):
        owner(persona, request)
        with generation_errors():
            return factory().store.list(persona, page=page, page_size=page_size, state=state, model=model,
                                        status=status, kind=kind, channel=channel)

    @router.get('/assets')
    def assets(persona: str, request: Request, page: int = 1, page_size: int = 30):
        owner(persona, request)
        with generation_errors():
            return factory().store.assets(persona, page, page_size)

    return router
