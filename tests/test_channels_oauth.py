from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from easel.channels import ChannelAdapter, ChannelError, Credential, CredentialStore, DEFINITIONS, OAuthApplication, OAuthService


@pytest.mark.parametrize('platform', ['youtube', 'tiktok', 'instagram', 'facebook', 'threads', 'twitter', 'linkedin', 'pinterest', 'google-business'])
def test_oauth_callback_binds_account_to_persona_and_consumes_state(tmp_path, platform):
    def provider(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if 'token' in path or 'accessToken' in path:
            return httpx.Response(200, json={'access_token': 'provider-access', 'refresh_token': 'provider-refresh', 'expires_in': 3600})
        profiles = {
            'youtube': {'items': [{'id': 'own-account', 'snippet': {'title': 'Me'}}]},
            'tiktok': {'data': {'user': {'open_id': 'own-account', 'display_name': 'Me'}}},
            'instagram': {'id': 'own-account', 'username': 'Me'},
            'facebook': {'id': 'own-account', 'name': 'Me'},
            'threads': {'id': 'own-account', 'username': 'Me'},
            'twitter': {'data': {'id': 'own-account', 'name': 'Me'}},
            'linkedin': {'sub': 'own-account', 'name': 'Me'},
            'pinterest': {'id': 'own-account', 'username': 'Me'},
            'google-business': {'sub': 'own-account', 'name': 'Me'},
        }
        return httpx.Response(200, json=profiles[platform])

    store = CredentialStore(tmp_path)
    application = OAuthApplication(client_id='client', client_secret=SecretStr('secret'), redirect_uri='http://localhost:7860/callback')
    oauth = OAuthService(store, lambda _: application, transport=httpx.MockTransport(provider))
    start = oauth.start('alice', platform)
    assert store.session_status('alice', platform, start['state'])['status'] == 'pending'
    parsed = parse_qs(urlsplit(start['authorization_url']).query)
    if DEFINITIONS[platform].pkce:
        assert parsed['code_challenge_method'] == ['S256']
    connected = oauth.exchange(platform, 'code', start['state'])
    restored = store.get('alice', platform, connected.account_uid)
    assert store.session_status('alice', platform, start['state'])['status'] == 'connected'
    assert restored.access_token.get_secret_value() == 'provider-access'
    assert restored.persona == 'alice'
    assert b'provider-access' not in (tmp_path / 'channels.sqlite3').read_bytes()
    with pytest.raises(ChannelError, match='oauth_state_invalid'):
        oauth.exchange(platform, 'code', start['state'])
    with pytest.raises(ChannelError, match='account_not_connected'):
        store.get('bob', platform, connected.account_uid)


def test_credential_ciphertext_cannot_be_moved_to_another_scope(tmp_path):
    store = CredentialStore(tmp_path)
    store.save(Credential(persona='alice', platform='twitter', account_uid='self', access_token=SecretStr('secret')))
    with store.connection() as db:
        encrypted = db.execute('SELECT encrypted FROM credentials').fetchone()[0]
        db.execute('INSERT INTO credentials VALUES(?,?,?,?)', ('bob', 'twitter', 'self', encrypted))
    with pytest.raises(ChannelError, match='credential_scope_mismatch'):
        store.get('bob', 'twitter', 'self')


def test_refresh_preserves_rotated_and_existing_tokens(tmp_path):
    store = CredentialStore(tmp_path)
    credential = Credential(persona='alice', platform='twitter', account_uid='self', access_token=SecretStr('old'), refresh_token=SecretStr('old-refresh'))
    store.save(credential)
    application = OAuthApplication(client_id='client', client_secret=SecretStr('client-secret'), redirect_uri='http://localhost/callback')
    def provider(request):
        assert parse_qs(request.content.decode())['refresh_token'] == ['old-refresh']
        return httpx.Response(200, json={'access_token': 'new', 'refresh_token': 'rotated', 'expires_in': 7200})
    oauth = OAuthService(store, lambda _: application, transport=httpx.MockTransport(provider))
    updated = oauth.refresh(credential)
    assert updated.refresh_token.get_secret_value() == 'rotated'
    assert store.get('alice', 'twitter', 'self').access_token.get_secret_value() == 'new'
