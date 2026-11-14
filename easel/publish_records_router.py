"""Filtered publication history and verified work-link updates."""
from collections.abc import Awaitable, Callable
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from easel.publish_ledger import PublishLedger
from easel.publish_records import PublishRecords
from easel.publish_projection import drain_publish_projections


class WorkLinkUpdate(BaseModel):
    persona: str
    status: str
    platform_work_id: str = ''
    permalink: str = ''


def create_router(ledger: Callable[[], PublishLedger], valid_profile: Callable[[str], bool],
                  require_write: Callable[[Request], None],
                  readback: Callable[[str, dict], Awaitable[dict]]) -> APIRouter:
    router = APIRouter(prefix='/api/publish-records')

    def store() -> PublishRecords:
        return PublishRecords(ledger())

    def owner(persona: str) -> str:
        if not valid_profile(persona):
            raise HTTPException(400, '画像不存在或名称非法')
        return persona

    def get(persona: str, run_id: str):
        try:
            return store().get(owner(persona), run_id)
        except KeyError as error:
            raise HTTPException(404, '发布记录不存在') from error

    @router.get('')
    def records(persona: str, platform: str = '', account_uid: str = '', status: str = '',
                start: datetime | None = None, end: datetime | None = None,
                offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        try:
            return store().list(owner(persona), platform=platform, account_uid=account_uid,
                                statuses=tuple(status.split(',')) if status else (), start=start, end=end, offset=offset, limit=limit)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.get('/queued')
    def queued(persona: str, platform: str = '', account_uid: str = ''):
        return store().list(owner(persona), platform=platform, account_uid=account_uid,
                            statuses=('prepared', 'approved', 'queued', 'running', 'submitted'))

    @router.get('/published')
    def published(persona: str, platform: str = '', account_uid: str = ''):
        return store().list(owner(persona), platform=platform, account_uid=account_uid, statuses=('published',))

    @router.get('/summary')
    def summary(persona: str, start: datetime | None = None, end: datetime | None = None):
        try:
            return store().summary(owner(persona), start, end)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.get('/public/{run_id}')
    def public_record(run_id: str):
        run = ledger().get_run(run_id)
        if run is None:
            raise HTTPException(404, '发布记录不存在')
        try:
            visible = store().get(run.persona, run_id)
        except KeyError as error:
            raise HTTPException(404, '发布记录不存在') from error
        return store().view(visible, public=True)

    @router.get('/{run_id}')
    def detail(run_id: str, persona: str):
        return store().view(get(persona, run_id))

    @router.delete('/{run_id}')
    def delete(run_id: str, persona: str, request: Request):
        require_write(request)
        get(persona, run_id)
        try:
            store().hide(persona, run_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {'deleted': True}

    @router.patch('/{run_id}/work-link')
    async def work_link(run_id: str, payload: WorkLinkUpdate, request: Request):
        require_write(request)
        run = get(payload.persona, run_id)
        if payload.status not in ('ready', 'pending', 'error'):
            raise HTTPException(400, '作品链接状态无效')
        if payload.status == 'ready':
            result = await readback(run.platform, {'persona': run.persona, 'run_id': run_id,
                'account_uid': run.account_id, 'platform_work_id': payload.platform_work_id,
                'permalink': payload.permalink})
            verified = result.get('state') == 'published'
            store().annotate_link(run.persona, run_id, 'ready' if verified else 'pending')
            return result
        store().annotate_link(run.persona, run_id, payload.status)
        return store().view(run)

    @router.get('/{run_id}/user-action')
    def user_action(run_id: str, persona: str):
        run = get(persona, run_id)
        action = run.payload.get('user_action')
        if isinstance(action, dict):
            return {key: value for key, value in action.items() if key in {'scheme', 'short_link', 'message', 'expires_at'}}
        if run.payload.get('channel_progress'):
            from easel._channel_publish_models import ChannelProgress
            from easel._channel_handoff import handoff_info
            progress = ChannelProgress.model_validate(run.payload['channel_progress'])
            handoff = handoff_info(run.platform, progress.receipt)
            return {'run_id': run_id, 'state': run.state.value, 'mode': 'official_api',
                    'requires_user_action': handoff.required, 'scheme': handoff.action_url,
                    'expires_at': handoff.expires_at}
        return {'run_id': run_id, 'state': run.state.value,
                'status_path': f'/api/publish/{run.platform}/status', 'mode': 'local_browser'}

    return router
