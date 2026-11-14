"""Native Ark video creation/status and the shared configured model catalog."""
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request

from easel.generation_router import generation_errors
from easel.generation_videos import VideoGeneration
from easel.generation_volcengine_models import VolcengineRequest


def create_router(factory: Callable[[], VideoGeneration], profile_valid: Callable[[str], bool],
                  require_access: Callable[[Request], None]) -> APIRouter:
    router = APIRouter(prefix='/api/ai', tags=['creation'])

    def owner(persona: str, request: Request) -> None:
        if request.method != 'GET' or not getattr(request.state, 'caller_key', None):
            require_access(request)
        if not profile_valid(persona):
            raise HTTPException(400, 'persona_invalid')

    @router.post('/volcengine/video')
    async def create(payload: VolcengineRequest, request: Request):
        owner(payload.persona, request)
        with generation_errors():
            service = factory()
            job = service.submit_volcengine(payload, str(request.base_url))
            if job.state == 'queued':
                await service.execute(job.id)
            return service.store.get(payload.persona, job.id, 'volcengine')

    @router.get('/volcengine/video/{identifier}')
    async def status(identifier: str, persona: str, request: Request):
        owner(persona, request)
        with generation_errors():
            service = factory()
            job = service.store.get(persona, identifier, 'volcengine')
            if job.state == 'waiting':
                await service.execute(job.id)
            return service.store.get(persona, identifier, 'volcengine')

    return router
