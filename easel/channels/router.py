"""Auth and read-only API surface for the official platform adapters."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
import time
from typing import Iterator
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from easel.channels import ChannelAdapter, ChannelError, Credential, CredentialStore, DEFINITIONS, OAuthApplication, OAuthService, definition
from easel.channels.models import PlatformId


class AccountSelection(BaseModel):
    model_config = ConfigDict(frozen=True)
    persona: str
    account_uid: str = ''
    selected_uid: str = ''


class AccountBulkDelete(BaseModel):
    persona: str
    account_uids: list[str] = Field(min_length=1, max_length=100)


class OAuthCallback(BaseModel):
    code: str = ''
    state: str = ''
    error: str = ''
    token: str = ''
    tickets: dict[str, str] = Field(default_factory=dict)
    nickname: str = ''
    avatar: str = ''


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except ChannelError as error:
        raise HTTPException(error.status, {'platform': error.platform, 'code': error.code}) from error


def application_from_settings(platform: PlatformId, settings: Mapping[str, str]) -> OAuthApplication:
    from easel.channels.native_definitions import NATIVE_IDS
    if platform in NATIVE_IDS:
        from easel.channels.native_config import native_application
        if platform == 'douyin-open' and settings.get('EASEL_DOUYIN_OPEN_AUTH_TYPE', 'qr').lower() != 'oauth2':
            from easel.channels.douyin_miniapp import DouyinMiniApp
            miniapp = DouyinMiniApp(settings)
            return OAuthApplication(client_id=miniapp.client_id, client_secret=SecretStr(miniapp.secret), redirect_uri='')
        return native_application(settings, platform)
    prefix = 'EASEL_' + platform.upper().replace('-', '_') + '_'
    client_id, redirect = settings.get(prefix + 'CLIENT_ID', ''), settings.get(prefix + 'REDIRECT_URI', '')
    if not client_id or not redirect:
        raise ChannelError(platform, 'oauth_application_not_configured', status=409)
    return OAuthApplication(client_id=client_id, client_secret=SecretStr(settings.get(prefix + 'CLIENT_SECRET', '')),
                            redirect_uri=redirect, scopes=tuple(settings.get(prefix + 'SCOPES', '').split()))


def create_router(root: Callable[[], Path], settings: Callable[[], Mapping[str, str]],
                  validate_profile: Callable[[str], bool], require_write: Callable[[Request], None]) -> APIRouter:
    """Publish mutations remain in Easel's ledger service and require explicit integration."""
    router = APIRouter(prefix='/api/channels', tags=['channels'])

    def owner(persona: str) -> str:
        if not persona or not validate_profile(persona):
            raise HTTPException(400, '画像不存在或名称非法')
        return persona

    def services() -> tuple[CredentialStore, OAuthService]:
        store = CredentialStore(root() / '_channels')
        return store, OAuthService(store, lambda platform: application_from_settings(platform, settings()), settings=settings())

    def credential(platform: str, persona: str, account_uid: str) -> Credential:
        spec = definition(platform)
        owner(persona)
        if not account_uid: raise HTTPException(400, 'account_uid 必填')
        store, oauth = services()
        value = store.get(persona, spec.platform, account_uid)
        if value.expires_at is not None and value.expires_at <= time.time() + 30:
            value = oauth.refresh(value)
        return value

    @router.get('/platforms')
    def platforms():
        from easel.channels.manifest import describe_platform
        values = settings()
        return [describe_platform(spec, values) for spec in DEFINITIONS.values()]

    @router.post('/{platform}/auth/start')
    def start(platform: str, payload: AccountSelection, request: Request):
        require_write(request)
        with _errors():
            return services()[1].start(owner(payload.persona), definition(platform).platform)

    @router.get('/{platform}/auth/callback')
    def callback(platform: str, code: str = '', state: str = '', error: str = ''):
        with _errors():
            spec = definition(platform)
            if error:
                services()[1].deny(spec.platform, state)
                raise HTTPException(400, {'code': 'oauth_authorization_denied'})
            value = services()[1].exchange(spec.platform, code, state, validate_persona=validate_profile)
            owner(value.persona)
            return {'connected': True, 'persona': value.persona, 'platform': value.platform,
                    'account_uid': value.account_uid, 'display_name': value.display_name}

    @router.post('/{platform}/auth/callback')
    async def callback_post(platform: str, request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 32768: raise HTTPException(413, 'OAuth callback too large')
        try:
            if request.headers.get('content-type', '').split(';')[0] == 'application/x-www-form-urlencoded':
                fields = parse_qs(body.decode(), keep_blank_values=True)
                if any(len(values) != 1 for values in fields.values()): raise ValueError('duplicate fields')
                payload = OAuthCallback.model_validate({key: values[0] for key, values in fields.items()})
            else:
                payload = OAuthCallback.model_validate_json(body)
        except ValueError as error:
            raise HTTPException(400, 'OAuth callback invalid') from error
        if platform == 'douyin-open' and payload.token:
            from easel.channels.native_oauth import exchange_miniapp
            with _errors():
                value = exchange_miniapp(services()[1], payload.state, payload.token, payload.tickets,
                    nickname=payload.nickname, avatar=payload.avatar, validate_persona=validate_profile)
                return {'connected': True, 'persona': value.persona, 'platform': value.platform,
                    'account_uid': value.account_uid, 'display_name': value.display_name}
        return callback(platform, payload.code, payload.state, payload.error)

    @router.get('/{platform}/accounts')
    def accounts(platform: str, persona: str):
        with _errors():
            values = services()[0].accounts(owner(persona), definition(platform).platform)
            return [{'account_uid': value.account_uid, 'display_name': value.display_name, 'parent_uid': value.parent_uid,
                     'expires_at': value.expires_at, 'scope': value.scope,
                     'profile': value.profile.model_dump() if value.profile else None} for value in values]

    @router.get('/{platform}/auth/status')
    def auth_session(platform: str, persona: str, state: str):
        with _errors():
            return services()[0].session_status(owner(persona), definition(platform).platform, state)

    @router.get('/{platform}/accounts/{account_uid}/auth-status')
    def auth_status(platform: str, account_uid: str, persona: str):
        with _errors():
            value = services()[0].get(owner(persona), definition(platform).platform, account_uid)
            expired = value.expires_at is not None and value.expires_at <= time.time()
            return {'account_uid': value.account_uid, 'status': 'expired' if expired else 'connected',
                    'expires_at': value.expires_at, 'refresh_available': bool(value.refresh_token.get_secret_value()) or platform in {'instagram', 'facebook', 'threads'}}

    @router.delete('/{platform}/accounts')
    def bulk_delete(platform: str, payload: AccountBulkDelete, request: Request):
        require_write(request)
        with _errors():
            services()[0].delete_many(owner(payload.persona), definition(platform).platform, payload.account_uids)
            return {'deleted': True, 'account_uids': payload.account_uids}

    @router.get('/{platform}/accounts/selectable')
    def selectable(platform: str, persona: str, account_uid: str):
        with _errors():
            return [value.model_dump() for value in ChannelAdapter(platform, settings=settings(), store=services()[0]).accounts(credential(platform, persona, account_uid))]

    @router.post('/{platform}/accounts/select')
    def select(platform: str, payload: AccountSelection, request: Request):
        require_write(request)
        with _errors():
            value = ChannelAdapter(platform, settings=settings(), store=services()[0]).select_account(credential(platform, payload.persona, payload.account_uid), payload.selected_uid)
            services()[0].save(value)
            return {'connected': True, 'account_uid': value.account_uid, 'display_name': value.display_name}

    @router.post('/{platform}/auth/refresh')
    def refresh(platform: str, payload: AccountSelection, request: Request):
        require_write(request)
        with _errors():
            value = services()[1].refresh(credential(platform, payload.persona, payload.account_uid))
            return {'refreshed': True, 'account_uid': value.account_uid, 'expires_at': value.expires_at}

    @router.delete('/{platform}/accounts/{account_uid}')
    def disconnect(platform: str, account_uid: str, persona: str, request: Request):
        require_write(request)
        with _errors():
            spec = definition(platform)
            value = credential(platform, persona, account_uid)
            remote_revoked = spec.platform not in {'threads', 'bilibili-open', 'kwai-open', 'wechat-official-open', 'wechat-channels-open', 'rednote-link'}
            if remote_revoked: services()[1].revoke(value)
            services()[0].delete(owner(persona), spec.platform, account_uid)
            return {'disconnected': True, 'remote_revoked': remote_revoked}

    @router.get('/{platform}/profile')
    def profile(platform: str, persona: str, account_uid: str):
        with _errors():
            return ChannelAdapter(platform, settings=settings(), store=services()[0]).profile(credential(platform, persona, account_uid)).model_dump()

    @router.get('/{platform}/works')
    def works(platform: str, persona: str, account_uid: str, cursor: str = '', limit: int = 25, direction: str = 'next'):
        with _errors():
            return ChannelAdapter(platform, settings=settings(), store=services()[0]).works(credential(platform, persona, account_uid), cursor, limit=limit, direction=direction)

    @router.get('/{platform}/works/{work_id}')
    def work(platform: str, work_id: str, persona: str, account_uid: str):
        with _errors():
            return ChannelAdapter(platform, settings=settings(), store=services()[0]).work(credential(platform, persona, account_uid), work_id)

    @router.get('/{platform}/work-link')
    def resolve_link(platform: str, persona: str, link: str, account_uid: str = '', data_id: str = ''):
        with _errors():
            if platform in {'rednote-link', 'bilibili-open', 'kwai-open', 'douyin-open'}:
                from easel.channels.links import native_link_info
                owner(persona)
                return native_link_info(platform, link, data_id)
            return ChannelAdapter(platform, settings=settings(), store=services()[0]).resolve_link(credential(platform, persona, account_uid), link)

    @router.post('/{platform}/works/{work_id}/ownership/verify')
    def verify_owner(platform: str, work_id: str, payload: AccountSelection):
        with _errors():
            ChannelAdapter(platform, settings=settings(), store=services()[0]).work(credential(platform, payload.persona, payload.account_uid), work_id)
            return {'verified': True, 'platform_work_id': work_id, 'account_uid': payload.account_uid}

    @router.get('/{platform}/analytics')
    def analytics(platform: str, persona: str, account_uid: str, work_id: str = '', start_date: str = '', end_date: str = ''):
        with _errors():
            from easel.analytics_snapshots import append_snapshot, normalize_snapshot
            from easel.channels.analytics import analytics_history, normalize_analytics
            account = credential(platform, persona, account_uid)
            raw = ChannelAdapter(platform, settings=settings(), store=services()[0]).analytics(account, work_id, start_date=start_date, end_date=end_date)
            normalized = normalize_analytics(account, raw, work_id)
            snapshot = normalize_snapshot(persona, platform, normalized, source='official_api')
            append_snapshot(root(), snapshot)
            history = analytics_history(account, raw, work_id)
            for point in history:
                append_snapshot(root(), normalize_snapshot(persona, platform, point, observed_at=str(point['observed_at']), source='official_api_history'))
            return {**normalized, 'snapshot_id': snapshot.snapshot_id, 'snapshots': history}

    @router.get('/{platform}/publish-options')
    def options(platform: str, persona: str, account_uid: str, keyword: str = '', latitude: float | None = None, longitude: float | None = None):
        with _errors():
            return ChannelAdapter(platform, settings=settings(), store=services()[0]).options(credential(platform, persona, account_uid), keyword=keyword, latitude=latitude, longitude=longitude)

    @router.get('/{platform}/locations')
    def locations(platform: str, persona: str, account_uid: str, business_account: str):
        with _errors():
            return ChannelAdapter(platform, settings=settings(), store=services()[0]).locations(credential(platform, persona, account_uid), business_account)

    @router.get('/{platform}/comments')
    def read_comments(platform: str, persona: str, account_uid: str, work_id: str, cursor: str = '', limit: int = 25, direction: str = 'next'):
        with _errors():
            return ChannelAdapter(platform, settings=settings(), store=services()[0]).comments(credential(platform, persona, account_uid), work_id, cursor, limit=limit, direction=direction)

    from easel.channels.native_router import add_native_routes
    add_native_routes(router, services, settings, owner, validate_profile, require_write)
    from easel.channels.mutation_router import add_mutation_routes
    from easel.channels.webhook_router import add_webhook_routes
    from easel.channels.group_router import add_group_routes
    from easel.channels.auxiliary_router import add_auxiliary_routes
    add_mutation_routes(router, credential, require_write)
    add_webhook_routes(router, lambda: services()[0], settings, credential)
    add_group_routes(router, lambda: services()[0], owner, require_write)
    add_auxiliary_routes(router, credential, require_write, lambda platform: ChannelAdapter(platform, settings=settings(), store=services()[0]))
    return router
