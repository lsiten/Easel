"""Owned native video create, remix, query and history endpoints."""
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request

from easel.generation_router import generation_errors
from easel.generation_video_models import OpenAIVideoRequest, VideoRemixRequest
from easel.generation_videos import VideoGeneration


def create_router(factory: Callable[[], VideoGeneration], profile_valid: Callable[[str], bool],
                  require_access: Callable[[Request], None]) -> APIRouter:
    router = APIRouter(prefix='/api/ai/openai', tags=['creation'])

    def owner(persona: str, request: Request) -> None:
        if request.method != 'GET' or not getattr(request.state, 'caller_key', None):
            require_access(request)
        if not profile_valid(persona):
            raise HTTPException(400, 'persona_invalid')

    @router.post('/videos')
    async def create_video(payload: OpenAIVideoRequest, request: Request):
        owner(payload.persona, request)
        with generation_errors():
            service = factory()
            job = service.submit(payload, str(request.base_url))
            if job.state == 'queued':
                await service.execute(job.id)
            return service.store.get(job.persona, job.id)

    @router.get('/videos')
    def videos(persona: str, request: Request, page: int = 1, page_size: int = 30):
        owner(persona, request)
        with generation_errors():
            return factory().store.list(persona, page, page_size, channel='openai')

    @router.get('/videos/{identifier}')
    async def video(identifier: str, persona: str, request: Request):
        owner(persona, request)
        with generation_errors():
            service = factory()
            job = service.store.get(persona, identifier, 'openai')
            if job.state == 'waiting':
                await service.execute(job.id)
            return service.store.get(persona, identifier)

    @router.post('/videos/{identifier}/remix')
    async def remix(identifier: str, payload: VideoRemixRequest, request: Request):
        owner(payload.persona, request)
        with generation_errors():
            service = factory()
            job = service.remix(identifier, payload)
            if job.state == 'queued':
                await service.execute(job.id)
            return service.store.get(payload.persona, job.id)

    return router
