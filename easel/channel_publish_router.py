"""HTTP boundary for reviewed, persona-owned official platform publishing."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError

from easel._channel_publish_models import (ChannelApproval, ChannelIntent,
    ChannelPublishRequest, ChannelRunView, ChannelScope, ChannelReconcileRequest, ChannelReconcileView)
from easel._channel_publish_models import ChannelReapproval, ChannelReapproveRequest
from easel.channel_publishing import ChannelPublishing
from easel.channels import ChannelAdapter, ChannelError, definition
from easel.content_library_models import ContentError
from easel.ownership import OwnershipError
from easel.publish_models import LedgerError
from easel.publish_projection import drain_publish_projections


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except ChannelError as error:
        raise HTTPException(error.status, {'platform': error.platform, 'code': error.code}) from error
    except OwnershipError as error:
        raise HTTPException(403, {'code': 'media_ownership_invalid', 'detail': str(error)}) from error
    except ContentError as error:
        raise HTTPException(error.status, {'code': error.code}) from error
    except LedgerError as error:
        raise HTTPException(409, {'code': type(error).__name__}) from error
    except ValidationError as error:
        raise HTTPException(409, {'code': 'stored_publication_invalid'}) from error


def create_router(root: Callable[[], Path], settings: Callable[[], Mapping[str, str]],
                  validate_profile: Callable[[str], bool], require_write: Callable[[Request], None], *,
                  adapter_factory: Callable[[str], ChannelAdapter] = ChannelAdapter) -> APIRouter:
    """Use the host's active output root, profile scope, settings, and write protection."""
    router = APIRouter(prefix='/api/channel-publish', tags=['channel-publish'])

    def service(persona: str) -> ChannelPublishing:
        if not validate_profile(persona):
            raise HTTPException(400, {'code': 'persona_invalid'})
        return ChannelPublishing(root(), settings, adapter_factory)

    def response(service: ChannelPublishing, view: ChannelRunView) -> ChannelRunView:
        if view.state == 'published':
            drain_publish_projections(service.ledger, service.root)
        return view

    @router.get('/media/{run_id}/{index}')
    def media(run_id: str, index: int, persona: str, expires: int, signature: str) -> FileResponse:
        with _errors():
            path = service(persona).media_file(run_id, index, persona, expires, signature)
            return FileResponse(path, headers={'Cache-Control': 'private, max-age=0', 'X-Content-Type-Options': 'nosniff'})

    @router.post('/{platform}/precheck')
    def precheck(platform: str, payload: ChannelIntent, request: Request) -> ChannelApproval:
        require_write(request)
        with _errors():
            return service(payload.persona).precheck(definition(platform).platform, payload)

    @router.post('/{platform}/publish')
    def publish(platform: str, payload: ChannelPublishRequest, request: Request) -> ChannelRunView:
        require_write(request)
        with _errors():
            active = service(payload.persona)
            return response(active, active.publish(definition(platform).platform, payload))

    @router.post('/{platform}/{run_id}/finalize')
    def finalize(platform: str, run_id: str, payload: ChannelScope, request: Request) -> ChannelRunView:
        require_write(request)
        with _errors():
            active = service(payload.persona)
            return response(active, active.finalize(definition(platform).platform, run_id, payload))

    @router.get('/{platform}/{run_id}')
    def get(platform: str, run_id: str, persona: str, account_uid: str) -> ChannelRunView:
        with _errors():
            return service(persona).get(definition(platform).platform, run_id,
                                       ChannelScope(persona=persona, account_uid=account_uid))

    @router.post('/{platform}/{run_id}/reconcile')
    def reconcile(platform: str, run_id: str, payload: ChannelReconcileRequest, request: Request) -> ChannelReconcileView:
        require_write(request)
        with _errors():
            active = service(payload.persona)
            view = active.reconcile(definition(platform).platform, run_id, payload)
            response(active, view)
            return view

    @router.post('/{platform}/{run_id}/reapprove/precheck')
    def reapprove_precheck(platform: str, run_id: str, payload: ChannelScope, request: Request) -> ChannelReapproval:
        require_write(request)
        with _errors():
            return service(payload.persona).reapprove_precheck(definition(platform).platform, run_id, payload)

    @router.post('/{platform}/{run_id}/reapprove')
    def reapprove(platform: str, run_id: str, payload: ChannelReapproveRequest, request: Request) -> ChannelRunView:
        require_write(request)
        with _errors():
            active = service(payload.persona)
            return response(active, active.reapprove(definition(platform).platform, run_id, payload))

    return router
