"""Platform OAuth exchange, refresh, and single-use PKCE authorization."""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import httpx
from typing import Callable, Mapping
from urllib.parse import urlencode, urlsplit

from pydantic import SecretStr

from easel.channels.definitions import definition
from easel.channels.http import ProviderHttp
from easel.channels.models import ChannelError, Credential, JsonDocument, OAuthApplication, OAuthPending, PlatformId
from easel.channels.store import CredentialStore
from easel.channels.token_values import token_expiry, token_identity


class OAuthService:
    def __init__(self, store: CredentialStore, applications: Callable[[PlatformId], OAuthApplication], *, transport: httpx.BaseTransport | None = None, settings: Mapping[str, str] | None = None) -> None:
        self.store = store
        self.applications = applications
        self.transport = transport
        self.settings = dict(settings or {})

    def start(self, persona: str, platform: PlatformId) -> JsonDocument:
        spec = definition(platform)
        application = self.applications(platform)
        from easel.channels.native_definitions import NATIVE_IDS
        if platform in NATIVE_IDS:
            from easel.channels.native_oauth import start_native
            return start_native(self, persona, platform, application)
        parsed = urlsplit(application.redirect_uri)
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}):
            raise ChannelError(platform, 'redirect_uri_invalid')
        verifier = secrets.token_urlsafe(48) if spec.pkce else ''
        pending = OAuthPending(persona=persona, platform=platform, verifier=SecretStr(verifier),
                               redirect_uri=application.redirect_uri, expires_at=time.time() + 600)
        state = self.store.issue_state(pending)
        params = {'client_id': application.client_id, 'redirect_uri': application.redirect_uri,
                  'response_type': 'code', 'state': state,
                  'scope': spec.scope_separator.join(application.scopes or spec.scopes)}
        if spec.token_auth == 'client_key':
            params['client_key'] = params.pop('client_id')
        if verifier:
            params.update(code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
                          code_challenge_method='S256')
        if platform in {'youtube', 'google-business'}:
            params.update(access_type='offline', prompt='consent')
        return {'authorization_url': spec.authorize_url + '?' + urlencode(params), 'state': state,
                'expires_at': pending.expires_at, 'platform': platform, 'persona': persona}

    def exchange(self, platform: PlatformId, code: str, state: str, *, validate_persona: Callable[[str], bool] | None = None) -> Credential:
        try:
            return self._exchange(platform, code, state, validate_persona=validate_persona)
        except ChannelError:
            self.store.fail_session(state, platform)
            raise

    def _exchange(self, platform: PlatformId, code: str, state: str, *, validate_persona: Callable[[str], bool] | None = None) -> Credential:
        if not code or len(code) > 8192 or not state:
            raise ChannelError(platform, 'oauth_callback_invalid')
        pending = self.store.consume_state(state, platform)
        if pending.verifier.get_secret_value() == 'miniapp_qr': raise ChannelError(platform, 'miniapp_callback_required')
        if validate_persona is not None and not validate_persona(pending.persona):
            raise ChannelError(platform, 'persona_not_found', status=404)
        application = self.applications(platform)
        if application.redirect_uri != pending.redirect_uri:
            raise ChannelError(platform, 'redirect_uri_changed', status=403)
        params = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': pending.redirect_uri}
        if pending.verifier.get_secret_value():
            params['code_verifier'] = pending.verifier.get_secret_value()
        payload = self._token(platform, application, params)
        credential = self._credential(platform, pending.persona, payload)
        from easel.channels.adapter import ChannelAdapter
        profile = ChannelAdapter(platform, transport=self.transport, settings=self.settings, store=self.store).profile(credential)
        credential = credential.model_copy(update={'account_uid': profile.account_uid, 'display_name': profile.display_name,
            'parent_uid': profile.parent_uid or credential.parent_uid, 'profile': profile})
        self.store.save(credential, oauth_state=state)
        return credential

    def deny(self, platform: PlatformId, state: str) -> None:
        self.store.consume_state(state, platform)
        self.store.fail_session(state, platform)

    def refresh(self, credential: Credential) -> Credential:
        with self.store.platform_lock(credential.persona, credential.platform):
            current = self.store.get(credential.persona, credential.platform, credential.account_uid)
            if current.access_token != credential.access_token or current.refresh_token != credential.refresh_token:
                return current
            if current.platform == 'facebook' and current.parent_uid:
                if current.parent_uid == current.account_uid: raise ChannelError('facebook', 'parent_account_invalid', status=403)
                parent = self.store.get(current.persona, 'facebook', current.parent_uid)
                if parent.expires_at is not None and parent.expires_at <= time.time() + 30: parent = self._refresh(parent)
                from easel.channels.adapter import ChannelAdapter
                updated = ChannelAdapter('facebook', transport=self.transport).select_account(parent, current.account_uid)
                self.store._save(updated)
                return updated
            return self._refresh(current)

    def _refresh(self, credential: Credential) -> Credential:
        platform = credential.platform
        application = self.applications(platform)
        http = ProviderHttp(platform, transport=self.transport)
        token = credential.access_token.get_secret_value()
        match platform:
            case 'instagram' | 'threads':
                host = 'https://graph.instagram.com' if platform == 'instagram' else 'https://graph.threads.net'
                grant = 'ig_refresh_token' if platform == 'instagram' else 'th_refresh_token'
                payload = http.send('GET', host + '/refresh_access_token', params={'grant_type': grant, 'access_token': token}).data
            case 'facebook':
                payload = self._token(platform, application, {'grant_type': 'fb_exchange_token', 'fb_exchange_token': token})
            case _:
                refresh = credential.refresh_token.get_secret_value()
                if not refresh:
                    raise ChannelError(platform, 'refresh_token_missing', status=409)
                payload = self._token(platform, application, {'grant_type': 'refresh_token', 'refresh_token': refresh})
        refreshed = self._credential(platform, credential.persona, payload, previous=credential)
        self.store._save(refreshed)
        return refreshed

    def revoke(self, credential: Credential) -> None:
        platform = credential.platform
        application = self.applications(platform)
        token = credential.access_token.get_secret_value()
        http = ProviderHttp(platform, transport=self.transport)
        match platform:
            case 'youtube' | 'google-business':
                http.send('POST', 'https://oauth2.googleapis.com/revoke', data={'token': token})
            case 'tiktok':
                http.send('POST', definition(platform).api_base + '/oauth/revoke/',
                          data={'client_key': application.client_id, 'client_secret': application.client_secret.get_secret_value(), 'token': token})
            case 'facebook' | 'instagram':
                http.send('DELETE', definition(platform).api_base + '/me/permissions', token=token)
            case 'twitter':
                http.send('POST', 'https://api.x.com/2/oauth2/revoke', data={'token': token, 'client_id': application.client_id},
                          basic=(application.client_id, application.client_secret.get_secret_value()))
            case 'pinterest':
                http.send('POST', 'https://api.pinterest.com/v5/oauth/revoke', data={'token': token},
                          basic=(application.client_id, application.client_secret.get_secret_value()))
            case 'linkedin':
                http.send('POST', 'https://api.linkedin.com/v2/oauth/v2/revoke',
                          data={'token': token, 'client_id': application.client_id, 'client_secret': application.client_secret.get_secret_value()})
            case 'threads':
                raise ChannelError(platform, 'remote_revoke_unsupported', status=405)
            case 'douyin-open':
                value = http.send('POST', 'https://open.douyin.com/oauth/revoke/', params={'open_id': credential.account_uid},
                    body={}, headers={'access-token': token}).data
                result = value.get('data')
                if isinstance(result, dict) and result.get('error_code') not in (None, 0, '0'): raise ChannelError(platform, 'provider_api_error', status=502)
            case 'bilibili-open' | 'kwai-open' | 'wechat-official-open' | 'wechat-channels-open' | 'rednote-link':
                raise ChannelError(platform, 'remote_revoke_unsupported', status=405)

    def _token(self, platform: PlatformId, application: OAuthApplication, params: Mapping[str, str]) -> JsonDocument:
        from easel.channels.native_definitions import NATIVE_IDS
        if platform in NATIVE_IDS:
            from easel.channels.native_oauth import native_token
            return native_token(platform, application, params, self.transport)
        spec = definition(platform)
        data = dict(params)
        basic = None
        match spec.token_auth:
            case 'basic':
                basic = (application.client_id, application.client_secret.get_secret_value())
                data['client_id'] = application.client_id
            case 'client_key':
                data.update(client_key=application.client_id, client_secret=application.client_secret.get_secret_value())
            case 'body' | 'graph':
                data.update(client_id=application.client_id, client_secret=application.client_secret.get_secret_value())
        http = ProviderHttp(platform, transport=self.transport)
        payload = http.send('GET', spec.token_url, params=data).data if spec.token_auth == 'graph' else http.send('POST', spec.token_url, data=data, basic=basic).data
        if platform in {'instagram', 'threads'} and 'code' in params:
            short = str(payload.get('access_token') or '')
            if not short:
                raise ChannelError(platform, 'token_response_invalid', status=502)
            host = 'https://graph.instagram.com' if platform == 'instagram' else 'https://graph.threads.net'
            grant = 'ig_exchange_token' if platform == 'instagram' else 'th_exchange_token'
            payload = http.send('GET', host + '/access_token', params={'grant_type': grant,
                                'client_secret': application.client_secret.get_secret_value(), 'access_token': short}).data
        if platform == 'facebook' and 'code' in params:
            short = str(payload.get('access_token') or '')
            if not short: raise ChannelError(platform, 'token_response_invalid', status=502)
            payload = http.send('GET', spec.token_url, params={'grant_type': 'fb_exchange_token',
                'fb_exchange_token': short, 'client_id': application.client_id,
                'client_secret': application.client_secret.get_secret_value()}).data
        return payload

    def _credential(self, platform: PlatformId, persona: str, payload: JsonDocument, *, previous: Credential | None = None) -> Credential:
        token = payload.get('access_token')
        if not isinstance(token, str) or not token:
            raise ChannelError(platform, 'token_response_invalid', status=502)
        expires_at = token_expiry(platform, payload)
        uid = token_identity(platform, payload, previous)
        refresh = payload.get('refresh_token') or (previous.refresh_token.get_secret_value() if previous else '')
        return Credential(persona=persona, platform=platform, account_uid=uid, access_token=SecretStr(token),
                          refresh_token=SecretStr(str(refresh)), expires_at=expires_at,
                          scope=' '.join(str(value) for value in payload['scopes']) if isinstance(payload.get('scopes'), list) else str(payload.get('scope') or (previous.scope if previous else '')),
                          display_name=previous.display_name if previous else '', parent_uid=previous.parent_uid if previous else '',
                          profile=previous.profile if previous else None, auxiliary_tokens=previous.auxiliary_tokens if previous else {})
