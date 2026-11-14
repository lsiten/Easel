"""AiToEarn task operations adapted to local persona-owned Easel sessions."""
from __future__ import annotations

import time
from collections.abc import Callable
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from easel.generation_agent import AgentService
from easel.generation_agent_models import CreateTask, RateTask, RenameTask, ShareTask, TaskUpdate
from easel.generation_router import generation_errors


def create_agent_router(factory: Callable[[], AgentService], authorize: Callable[[Request, str], str]) -> APIRouter:
    """authorize validates local access and returns the effective persona (never trust query alone)."""
    router = APIRouter(prefix='/api/ai/agent', tags=['agent-tasks'])

    @router.post('/tasks')
    async def create(payload: CreateTask, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with generation_errors():
            service = factory()
            run = await service.create(owner, payload)
        return StreamingResponse(service.stream(run, payload.includePartialMessages), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'})

    @router.get('/tasks')
    async def listing(request: Request, persona: str = '', page: int = Query(1, ge=1),
                      pageSize: int = Query(30, ge=1, le=200), keyword: str = Query('', max_length=100), favoriteOnly: bool = False):
        owner = authorize(request, persona)
        with generation_errors():
            service = factory()
            result = service.store.list(owner, page=page, size=pageSize, keyword=keyword, favorite=favoriteOnly)
            for task in result.items:
                await service.refresh(owner, task.id)
            return service.store.list(owner, page=page, size=pageSize, keyword=keyword, favorite=favoriteOnly)

    @router.get('/tasks/shared/{token}')
    async def shared(token: str):
        with generation_errors():
            return factory().public(token)

    @router.get('/tasks/{task_id}')
    async def detail(task_id: str, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with generation_errors():
            return await factory().refresh(owner, task_id)

    @router.get('/tasks/{task_id}/messages')
    async def messages(task_id: str, request: Request, persona: str = '', lastMessageId: str = ''):
        owner = authorize(request, persona)
        with generation_errors():
            return await factory().messages(owner, task_id, lastMessageId)

    @router.delete('/tasks/{task_id}')
    async def delete(task_id: str, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with generation_errors():
            await factory().delete(owner, task_id)
        return {'deleted': True}

    @router.patch('/tasks/{task_id}')
    async def rename(task_id: str, payload: RenameTask, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with generation_errors():
            store = factory().store
            store.update(store.get(owner, task_id), TaskUpdate(title=payload.title))
        return {'updated': True}

    @router.post('/tasks/{task_id}/rating')
    async def rating(task_id: str, payload: RateTask, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with generation_errors():
            service = factory()
            task = await service.refresh(owner, task_id)
            service.store.update(task, TaskUpdate(rating=payload.rating, ratingComment=payload.comment))
        return {'updated': True}

    @router.post('/tasks/{task_id}/share')
    async def share(task_id: str, payload: ShareTask, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with generation_errors():
            service = factory()
            return service.store.share(await service.refresh(owner, task_id), payload.ttlSeconds)

    @router.post('/tasks/{task_id}/abort')
    async def abort(task_id: str, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with generation_errors():
            await factory().abort(owner, task_id)
        return {'aborted': True}

    @router.post('/tasks/{task_id}/favorite')
    async def favorite(task_id: str, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with generation_errors():
            store = factory().store
            store.update(store.get(owner, task_id), TaskUpdate(favoritedAt=time.time()))
        return {'updated': True}

    @router.delete('/tasks/{task_id}/favorite')
    async def unfavorite(task_id: str, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with generation_errors():
            store = factory().store
            store.update(store.get(owner, task_id), TaskUpdate(favoritedAt=None))
        return {'updated': True}

    return router
