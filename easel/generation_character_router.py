"""Owner-scoped Sora character create/query adaptation routes."""
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request

from easel.generation_character_models import CharacterRequest
from easel.generation_characters import CharacterGeneration
from easel.generation_router import generation_errors


def create_router(factory: Callable[[], CharacterGeneration], profile_valid: Callable[[str], bool],
                  require_access: Callable[[Request], None]) -> APIRouter:
    router = APIRouter(prefix='/api/ai/openai/characters', tags=['creation'])

    def owner(persona: str, request: Request) -> None:
        if request.method != 'GET' or not getattr(request.state, 'caller_key', None):
            require_access(request)
        if not profile_valid(persona):
            raise HTTPException(400, 'persona_invalid')

    @router.post('')
    async def create(payload: CharacterRequest, request: Request):
        owner(payload.persona, request)
        with generation_errors():
            return await factory().submit(payload)

    @router.get('/{identifier}')
    async def detail(identifier: str, persona: str, request: Request):
        owner(persona, request)
        with generation_errors():
            return await factory().get(persona, identifier)

    return router
