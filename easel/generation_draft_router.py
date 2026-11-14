"""Draft-generation source operations and authenticated internal aliases."""
from __future__ import annotations
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Literal
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import ValidationError
from easel.content_library_models import ContentError
from easel.generation_draft import DraftGeneration
from easel.generation_draft_models import (ContentType, DraftSelection, ImageDraftInput, MemoryRegenerate,
    URLDraftInput, VideoDraftInput)
from easel.generation_models import GenerationError, GenerationModel
from easel.remote_media import RemoteMediaError
from easel.ownership import OwnershipError
from easel.generation_draft_inputs import normalized_draft_images


class InternalVideo(VideoDraftInput):
    userId: str
    userType: Literal['user', 'admin', 'system'] = 'user'


class InternalImage(ImageDraftInput):
    userId: str
    userType: Literal['user', 'admin', 'system'] = 'user'


class InternalTask(GenerationModel):
    userId: str
    userType: Literal['user', 'admin', 'system'] = 'user'
    taskId: str


@contextmanager
def draft_errors() -> Iterator[None]:
    try:
        yield
    except (GenerationError, ContentError) as error:
        raise HTTPException(error.status, error.code) from error
    except OwnershipError as error:
        raise HTTPException(403, 'generation_input_ownership_invalid') from error
    except RemoteMediaError as error:
        raise HTTPException(400, 'draft_remote_url_rejected') from error
    except ValidationError as error:
        raise HTTPException(502, 'draft_provider_result_invalid') from error


def create_draft_router(factory: Callable[[], DraftGeneration], authorize: Callable[[Request, str], str]) -> APIRouter:
    router = APIRouter(prefix='/api/ai/draft-generation', tags=['draft-generation'])

    @router.get('/stats')
    async def stats(request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with draft_errors():
            return factory().store.stats(owner)

    @router.get('')
    @router.get('/')
    async def listing(request: Request, persona: str = '', page: int = Query(1, ge=1), pageSize: int = Query(30, ge=1, le=200)):
        owner = authorize(request, persona)
        with draft_errors():
            return factory().store.list(owner, page, pageSize)

    @router.post('/query')
    async def query(payload: DraftSelection, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with draft_errors():
            store = factory().store
            return tuple(store.get(owner, identifier) for identifier in payload.taskIds)

    @router.get('/pricing')
    async def pricing(request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with draft_errors():
            return factory().runtime.pricing(owner)

    @router.get('/memory')
    async def memories(request: Request, persona: str = '', contentType: ContentType | None = None):
        owner = authorize(request, persona)
        with draft_errors():
            return factory().memory.list(owner, contentType)

    @router.delete('/memory/items/{item_id}')
    async def delete_memory(item_id: str, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with draft_errors():
            return factory().memory.delete(owner, item_id)

    @router.post('/memory/regenerate')
    async def regenerate(payload: MemoryRegenerate, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with draft_errors():
            return await factory().memory.regenerate(owner, payload)

    @router.get('/{identifier}')
    async def detail(identifier: str, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with draft_errors():
            return factory().store.get(owner, identifier)

    @router.post('/v2')
    async def video(payload: VideoDraftInput, request: Request, persona: str = '',
                    idempotency_key: str = Header(default='', max_length=200)):
        owner = authorize(request, persona)
        with draft_errors():
            return factory().submit(owner, normalized_draft_images(payload, str(request.base_url)), idempotency_key or uuid.uuid4().hex)

    @router.post('/image-text')
    async def image(payload: ImageDraftInput, request: Request, persona: str = '',
                    idempotency_key: str = Header(default='', max_length=200)):
        owner = authorize(request, persona)
        with draft_errors():
            return factory().submit(owner, normalized_draft_images(payload, str(request.base_url)), idempotency_key or uuid.uuid4().hex)

    @router.post('/from-video-url')
    async def from_url(payload: URLDraftInput, request: Request, persona: str = '',
                       idempotency_key: str = Header(default='', max_length=200)):
        owner = authorize(request, persona)
        with draft_errors():
            service = factory()
            created = service.submit(owner, payload, idempotency_key or uuid.uuid4().hex)
            await service.execute(created.taskIds[0])
            job = service.store.get(owner, created.taskIds[0])
            if job.state != 'succeeded' or job.response is None:
                raise GenerationError(job.errorMessage or 'draft_still_generating', 409)
            return {'materialId': job.response.materialId}

    return router


def create_internal_draft_router(factory: Callable[[], DraftGeneration], authorize: Callable[[Request, str], str]) -> APIRouter:
    """Internal aliases remain owner-bound; body userId never grants access."""
    router = APIRouter(prefix='/api/internal/ai/draft-generation', tags=['internal-draft-generation'])

    @router.post('/v2')
    async def video(payload: InternalVideo, request: Request, idempotency_key: str = Header(default='', max_length=200)):
        owner = authorize(request, payload.userId)
        with draft_errors():
            clean = VideoDraftInput.model_validate(payload.model_dump(exclude={'userId', 'userType'}))
            return factory().submit(owner, normalized_draft_images(clean, str(request.base_url)), idempotency_key or uuid.uuid4().hex)

    @router.post('/image-text')
    async def image(payload: InternalImage, request: Request, idempotency_key: str = Header(default='', max_length=200)):
        owner = authorize(request, payload.userId)
        with draft_errors():
            clean = ImageDraftInput.model_validate(payload.model_dump(exclude={'userId', 'userType'}))
            return factory().submit(owner, normalized_draft_images(clean, str(request.base_url)), idempotency_key or uuid.uuid4().hex)

    @router.post('/task')
    async def task(payload: InternalTask, request: Request):
        owner = authorize(request, payload.userId)
        with draft_errors():
            return factory().store.get(owner, payload.taskId)

    return router
