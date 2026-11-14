"""Registered dynamic option sources and owned YouTube service resources."""
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StrictBool

from easel.channels.adapter import ChannelAdapter
from easel.channels.models import ChannelError, Credential
from easel.channels.youtube import add_to_playlist, list_captions, list_playlists


class PlaylistAdd(BaseModel):
    persona: str
    account_uid: str
    work_id: str
    reviewed: StrictBool = False


def add_auxiliary_routes(router: APIRouter, credential: Callable[[str, str, str], Credential], require_write: Callable[[Request], None],
                         adapter_factory: Callable[[str], ChannelAdapter] = ChannelAdapter) -> None:
    def execute(operation: Callable[[], object]):
        try:
            return operation()
        except ChannelError as error:
            raise HTTPException(error.status, {'platform': error.platform, 'code': error.code}) from error

    @router.get('/youtube/captions')
    def captions(persona: str, account_uid: str, work_id: str):
        return execute(lambda: list_captions(ChannelAdapter('youtube').reader, credential('youtube', persona, account_uid), work_id))

    @router.get('/youtube/playlists')
    def playlists(persona: str, account_uid: str, cursor: str = ''):
        if len(cursor) > 2048: raise HTTPException(400, 'cursor invalid')
        return execute(lambda: list_playlists(ChannelAdapter('youtube').reader, credential('youtube', persona, account_uid), cursor))

    @router.post('/youtube/playlists/{playlist_id}/videos')
    def playlist_add(playlist_id: str, payload: PlaylistAdd, request: Request):
        require_write(request)
        if not payload.reviewed: raise HTTPException(409, '请先审核并确认平台操作')
        return execute(lambda: add_to_playlist(ChannelAdapter('youtube').reader,
            credential('youtube', payload.persona, payload.account_uid), playlist_id, payload.work_id))

    @router.get('/{platform}/publish-options/{field}/values')
    def values(platform: str, field: str, persona: str, account_uid: str, keyword: str = '',
               latitude: float | None = None, longitude: float | None = None, category_id: str = '', region_code: str = 'US'):
        def read():
            account, adapter = credential(platform, persona, account_uid), adapter_factory(platform)
            if platform == 'bilibili-open' and field == 'tid': return adapter.options(account)
            if platform == 'youtube' and field in {'categoryId', 'category_id'}:
                return adapter.reader.call('GET', '/videoCategories', account,
                    params={'part': 'snippet', **({'id': category_id} if category_id else {'regionCode': region_code})}).data
            if platform == 'pinterest' and field in {'boardId', 'board_id'}: return adapter.options(account)
            if platform == 'threads' and field == 'location_id':
                if not keyword and (latitude is None or longitude is None): raise ChannelError(platform, 'location_filter_required')
                return adapter.options(account, keyword=keyword, latitude=latitude, longitude=longitude)
            raise ChannelError(platform, 'publish_option_source_unsupported', status=405)
        return execute(read)
