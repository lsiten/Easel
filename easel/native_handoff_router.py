"""Owner administration and sealed public native-app handoff surfaces."""
from collections.abc import Callable, Mapping
import io
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
import httpx
import segno

from easel.channel_publish_router import _errors
from easel.native_handoff import NativeHandoff
from easel.native_handoff_models import HandoffPlatform, HandoffStart, HandoffTemplate, ShareConfigRequest
from easel.publish_models import RunState
from easel.publish_recovery import worker_owner
from easel.rednote_share import share_config


def create_router(root: Callable[[], Path], settings: Callable[[], Mapping[str, str]],
                  valid_profile: Callable[[str], bool], require_write: Callable[[Request], None], *,
                  transport: httpx.BaseTransport | None = None) -> APIRouter:
    router = APIRouter(prefix='/api/native-handoff', tags=['native-handoff'])

    def service() -> NativeHandoff:
        return NativeHandoff(root(), settings, transport=transport)

    def owner(persona: str, request: Request) -> None:
        require_write(request)
        if not valid_profile(persona):
            raise HTTPException(400, '画像不存在或名称非法')

    @router.post('/{platform}/templates')
    def create(platform: HandoffPlatform, payload: HandoffTemplate, request: Request):
        owner(payload.persona, request)
        with _errors():
            return service().create(platform, payload)

    @router.get('/{platform}/templates')
    def templates(platform: HandoffPlatform, persona: str, request: Request):
        owner(persona, request)
        with _errors():
            active = service()
            return [active.owner_view(run) for run in active.ledger.list_runs(persona=persona, platform='native-template:' + platform)]

    @router.get('/{platform}/templates/{template_id}')
    def detail(platform: HandoffPlatform, template_id: str, persona: str, request: Request):
        owner(persona, request)
        with _errors():
            active = service()
            run = active.owned(persona, template_id)
            if run.payload['native_platform'] != platform:
                raise HTTPException(404, '模板不存在')
            return active.owner_view(run)

    @router.delete('/{platform}/templates/{template_id}')
    def revoke(platform: HandoffPlatform, template_id: str, persona: str, request: Request):
        owner(persona, request)
        with _errors():
            active = service()
            run = active.owned(persona, template_id)
            if run.payload['native_platform'] != platform:
                raise HTTPException(404, '模板不存在')
            if run.state != RunState.CANCELLED:
                lease = active.ledger.acquire_lease(run.run_id, worker_owner())
                try:
                    active.ledger.transition(run.run_id, expected=run.state, target=RunState.CANCELLED,
                        actor='template-owner', fencing_token=lease.fencing_token)
                finally:
                    active.ledger.release_lease(run.run_id, lease.owner, lease.fencing_token)
            return {'revoked': True}

    @router.get('/templates/{template_id}')
    def public(template_id: str, token: str):
        with _errors():
            return service().public_view(template_id, token)

    @router.get('/templates/{template_id}/media/{index}')
    def media(template_id: str, index: int, token: str):
        with _errors():
            return FileResponse(service().media(template_id, index, token),
                headers={'Cache-Control': 'private, max-age=0', 'X-Content-Type-Options': 'nosniff'})

    @router.get('/templates/{template_id}/qr')
    def qr(template_id: str, token: str):
        with _errors():
            active = service()
            run = active.template(template_id, token)
            url = active.owner_view(run)['public_url']
            if not isinstance(url, str) or not url:
                raise HTTPException(409, '请配置 EASEL_CHANNEL_MEDIA_BASE_URL')
            output = io.BytesIO()
            segno.make(url).save(output, kind='png', scale=5)
            return Response(output.getvalue(), media_type='image/png', headers={'Cache-Control': 'private, max-age=0'})

    @router.post('/templates/{template_id}/start')
    def start(template_id: str, token: str, payload: HandoffStart):
        with _errors():
            return service().start(template_id, token, payload)

    @router.get('/templates/{template_id}/runs/{run_id}')
    def status(template_id: str, run_id: str, token: str):
        with _errors():
            return service().status(template_id, token, run_id)

    @router.post('/rednote-link/share-config')
    def rednote(payload: ShareConfigRequest, request: Request):
        owner(payload.persona, request)
        with _errors():
            return share_config(settings(), payload.nonce, transport=transport)

    return router
