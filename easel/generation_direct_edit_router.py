"""Persona-authorized DirectEdit business endpoints."""
from collections.abc import Callable
from fastapi import APIRouter, HTTPException, Request
from easel.generation_direct_edit_models import DirectEditRequest
from easel.generation_direct_edit_service import DirectEditGeneration, public_job
from easel.generation_router import generation_errors


def create_router(factory: Callable[[], DirectEditGeneration], profile_valid: Callable[[str], bool],
                  require_access: Callable[[Request], None]) -> APIRouter:
    router = APIRouter(prefix='/api/ai/aideo/video-edit/tasks', tags=['video-edit'])

    def owner(persona: str, request: Request) -> None:
        if request.method != 'GET' or not getattr(request.state, 'caller_key', None):
            require_access(request)
        if not profile_valid(persona):
            raise HTTPException(400, 'persona_invalid')

    @router.post('')
    async def submit(payload: DirectEditRequest, request: Request):
        owner(payload.persona, request)
        with generation_errors():
            return public_job(await factory().submit(payload))

    @router.get('/{task_id}')
    async def detail(task_id: str, persona: str, request: Request):
        owner(persona, request)
        with generation_errors():
            return public_job(await factory().get(persona, task_id))

    return router
