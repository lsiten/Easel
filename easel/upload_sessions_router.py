"""Authenticated issuance and expiring capability upload/confirm routes."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from easel.content_library_router import content_errors
from easel.upload_sessions import UploadSessions
from easel.upload_sessions_models import CompletedUpload, UploadAccess, UploadReceipt, UploadRequest


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    max_bytes: int
    extensions: frozenset[str]
    accept_upload: Callable[[CompletedUpload], UploadReceipt]


def create_router(root: Callable[[], Path], profile_valid: Callable[[str], bool],
                  require_write: Callable[[Request], None], *, policy: UploadPolicy) -> APIRouter:
    router = APIRouter(prefix='/api/asset-uploads', tags=['asset-uploads'])

    def owner(persona: str) -> str:
        if not profile_valid(persona):
            raise HTTPException(400, 'persona_invalid')
        return persona

    def store() -> UploadSessions:
        return UploadSessions(root(), max_bytes=policy.max_bytes, extensions=policy.extensions)

    @router.post('/sessions')
    def issue(payload: UploadRequest, request: Request):
        require_write(request)
        owner(payload.persona)
        with content_errors():
            return store().create(payload)

    @router.get('/{upload_id}')
    def status(upload_id: str, persona: str):
        with content_errors():
            return store().status(upload_id, UploadAccess(persona=owner(persona)))

    @router.put('/{upload_id}/content')
    async def receive(upload_id: str, persona: str, request: Request):
        require_write(request)
        with content_errors():
            session = await store().receive(upload_id, UploadAccess(persona=owner(persona)), request.stream())
            return {'id': session.id, 'state': session.state}

    @router.post('/{upload_id}/confirm')
    def confirm(upload_id: str, persona: str, request: Request):
        require_write(request)
        with content_errors():
            return store().confirm(upload_id, UploadAccess(persona=owner(persona)), policy.accept_upload)

    @router.put('/public/{upload_id}/content')
    async def public_receive(upload_id: str, token: str, request: Request):
        with content_errors():
            session = await store().receive(upload_id, UploadAccess(token=token), request.stream())
            return {'id': session.id, 'state': session.state}

    @router.post('/public/{upload_id}/confirm')
    def public_confirm(upload_id: str, token: str):
        with content_errors():
            return store().confirm(upload_id, UploadAccess(token=token), policy.accept_upload)

    @router.post('/public/{upload_id}/callback')
    def callback(upload_id: str, token: str):
        with content_errors():
            return store().confirm(upload_id, UploadAccess(token=token), policy.accept_upload)

    return router
