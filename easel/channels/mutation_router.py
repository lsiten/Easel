"""Reviewed official-platform mutations using persona-scoped stored credentials."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from easel.channels.adapter import ChannelAdapter
from easel.channels.models import ChannelError, Credential, PublishContent


class ReviewedAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    persona: str
    account_uid: str
    reviewed: StrictBool = False
    text: str = ''
    action: str = ''
    reply_to_id: str = ''
    work_id: str = ''


class WorkUpdate(ReviewedAction):
    content: PublishContent


class BoardCreate(ReviewedAction):
    name: str
    description: str = ''
    privacy: str = 'PUBLIC'


def add_mutation_routes(router: APIRouter, credential: Callable[[str, str, str], Credential],
                        require_write: Callable[[Request], None]) -> None:
    def execute(platform: str, payload: ReviewedAction, request: Request, operation: Callable[[ChannelAdapter, Credential], object]):
        require_write(request)
        if not payload.reviewed: raise HTTPException(409, '请先审核并确认平台操作')
        try:
            account = credential(platform, payload.persona, payload.account_uid)
            return operation(ChannelAdapter(platform), account)
        except ChannelError as error:
            raise HTTPException(error.status, {'platform': error.platform, 'code': error.code}) from error

    @router.patch('/{platform}/works/{work_id}')
    def update(platform: str, work_id: str, payload: WorkUpdate, request: Request):
        return execute(platform, payload, request, lambda adapter, account: adapter.update(account, work_id, payload.content))

    @router.delete('/{platform}/works/{work_id}')
    def delete(platform: str, work_id: str, payload: ReviewedAction, request: Request):
        def remove(adapter: ChannelAdapter, account: Credential):
            adapter.delete(account, work_id)
            return {'deleted': True, 'platform_work_id': work_id}
        return execute(platform, payload, request, remove)

    @router.post('/{platform}/works/{work_id}/comments')
    def comment(platform: str, work_id: str, payload: ReviewedAction, request: Request):
        return execute(platform, payload, request, lambda adapter, account: adapter.comment(account, work_id, payload.text, reply_to_id=payload.reply_to_id))

    @router.delete('/{platform}/comments/{comment_id}')
    def delete_comment(platform: str, comment_id: str, payload: ReviewedAction, request: Request):
        return execute(platform, payload, request, lambda adapter, account: adapter.delete_comment(account, comment_id))

    @router.post('/{platform}/engagement/{target_id}')
    def engage(platform: str, target_id: str, payload: ReviewedAction, request: Request):
        return execute(platform, payload, request, lambda adapter, account: adapter.engage(account, payload.action, target_id, text=payload.text, work_id=payload.work_id))

    @router.post('/{platform}/boards')
    def create_board(platform: str, payload: BoardCreate, request: Request):
        return execute(platform, payload, request, lambda adapter, account: adapter.create_board(account, payload.name,
                       description=payload.description, privacy=payload.privacy))
