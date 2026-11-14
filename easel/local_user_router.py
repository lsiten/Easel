"""Local current-user and AI preference endpoints."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request

from easel.content_library_router import content_errors
from easel.local_user import LocalUsers
from easel.local_user_models import AIConfiguration, AIKind, AIPreference, AIPreferences, UserPatch


def create_router(root: Callable[[], Path], profile_valid: Callable[[str], bool],
                  require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter(prefix='/api/local-user', tags=['local-user'])

    def owner(persona: str) -> str:
        if not profile_valid(persona):
            raise HTTPException(400, 'persona_invalid')
        return persona

    @router.get('')
    def current(persona: str):
        with content_errors():
            return LocalUsers(root()).read(owner(persona))

    @router.put('')
    def update(persona: str, payload: UserPatch, request: Request):
        require_write(request)
        with content_errors():
            return LocalUsers(root()).update(owner(persona), payload)

    @router.put('/ai')
    def ai_bulk(persona: str, payload: AIConfiguration, request: Request):
        require_write(request)
        with content_errors():
            return LocalUsers(root()).set_ai(owner(persona), AIPreferences.model_validate(payload.model_dump()))

    @router.put('/ai/{kind}')
    def ai_item(kind: AIKind, persona: str, payload: AIPreference, request: Request):
        require_write(request)
        with content_errors():
            return LocalUsers(root()).set_ai_kind(owner(persona), kind, payload)

    return router
