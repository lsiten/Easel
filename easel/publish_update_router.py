"""Explicit reviewed edits for existing published official-platform tasks."""
from collections.abc import Callable, Mapping
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from easel.channel_publish_router import _errors
from easel.channels import ChannelAdapter
from easel.publish_projection import drain_publish_projections
from easel.publish_update_models import UpdateApproval, UpdateIntent, UpdateRequest, UpdateView
from easel.publish_updates import PublishUpdates


def create_router(root: Callable[[], Path], settings: Callable[[], Mapping[str, str]],
                  validate_profile: Callable[[str], bool], require_write: Callable[[Request], None], *,
                  adapter_factory: Callable[[str], ChannelAdapter] = ChannelAdapter) -> APIRouter:
    router = APIRouter(prefix='/api/publish-flows/tasks/{task_id}/update', tags=['publication-updates'])

    def service(persona: str) -> PublishUpdates:
        if not validate_profile(persona):
            raise HTTPException(400, {'code': 'persona_invalid'})
        return PublishUpdates(root(), settings, adapter_factory)

    @router.post('/precheck')
    def precheck(task_id: str, payload: UpdateIntent, request: Request) -> UpdateApproval:
        require_write(request)
        with _errors():
            return service(payload.persona).precheck(task_id, payload)

    @router.post('')
    def update(task_id: str, payload: UpdateRequest, request: Request) -> UpdateView:
        require_write(request)
        with _errors():
            active = service(payload.persona)
            view = active.update(task_id, payload)
            drain_publish_projections(active.ledger, active.root)
            return view

    @router.get('/{update_id}')
    def get(task_id: str, update_id: str, persona: str, account_uid: str) -> UpdateView:
        with _errors():
            return service(persona).get(task_id, update_id, UpdateIntent(persona=persona, account_uid=account_uid))

    return router
