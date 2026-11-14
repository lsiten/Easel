"""Native identity callbacks use server verification before credential persistence."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr, ConfigDict

from easel.channels.adapter import ChannelAdapter
from easel.channels.models import ChannelError, Credential
from easel.channels.native_oauth import exchange_miniapp


class MiniappCallback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state: str = Field(min_length=1, max_length=256)
    token: str = Field(min_length=1, max_length=8192)
    tickets: dict[str, str]
    nickname: str = Field(default='', max_length=200)
    avatar: str = Field(default='', max_length=2048)


class CookieBinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    persona: str
    cookie: SecretStr = Field(min_length=1, max_length=32768)


def add_native_routes(router: APIRouter, services, settings, owner, validate_profile, require_write) -> None:
    @router.post('/douyin-open/auth/miniapp-callback')
    def miniapp_callback(payload: MiniappCallback):
        try:
            value = exchange_miniapp(services()[1], payload.state, payload.token, payload.tickets,
                nickname=payload.nickname, avatar=payload.avatar, validate_persona=validate_profile)
            return {'connected': True, 'persona': value.persona, 'platform': value.platform,
                'account_uid': value.account_uid, 'display_name': value.display_name}
        except ChannelError as error:
            raise HTTPException(error.status, {'platform': error.platform, 'code': error.code}) from error

    @router.post('/wechat-channels-open/accounts/bind-cookie')
    def bind_cookie(payload: CookieBinding, request: Request):
        require_write(request)
        try:
            store = services()[0]
            value = Credential(persona=owner(payload.persona), platform='wechat-channels-open',
                account_uid='pending', access_token=payload.cookie)
            profile = ChannelAdapter('wechat-channels-open', settings=settings(), store=store).profile(value)
            value = value.model_copy(update={'account_uid': profile.account_uid, 'display_name': profile.display_name, 'profile': profile})
            store.save(value)
            return {'connected': True, 'persona': value.persona, 'platform': value.platform,
                'account_uid': value.account_uid, 'display_name': value.display_name}
        except ChannelError as error:
            raise HTTPException(error.status, {'platform': error.platform, 'code': error.code}) from error
