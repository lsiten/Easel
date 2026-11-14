"""Application setup enters the real OAuth owner and keeps both connection paths independent."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

from easel.channels import OAuthService
from easel.channels import router as channels
from web import app as web


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    env = tmp_path / '.env'
    env.write_text('# existing configuration\nKEEP_VALUE=keep\n')
    monkeypatch.setattr(web, 'ENV_FILE', env)
    monkeypatch.setattr(web, 'profile_exists', lambda persona: persona in {'alice', 'bob'})
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex'})
    monkeypatch.setattr(web.harnesses, 'describe', lambda runtime: {'installed': True})
    monkeypatch.setattr(web, '_account_logged_in', lambda platform, cfg, persona: platform == 'xiaohongshu' and persona == 'alice')
    client = TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000))
    return client, env


def test_one_click_setup_saves_real_config_then_connects_through_existing_oauth(setup, monkeypatch):
    client, env = setup
    endpoint = '/api/channel-applications/youtube'
    request = {'persona': 'alice', 'session_id': 'setup-session'}
    started = client.post(endpoint + '/setup', json=request)
    assert started.status_code == 200, started.text
    identifier = started.json()['setup_id']
    assert client.post(endpoint + '/setup', json=request).json()['setup_id'] == identifier
    progress = endpoint + '/setup/' + identifier
    waiting = client.post(progress, json={'persona': 'alice', 'state': 'waiting_user', 'message': '请登录开发者控制台', 'task_space_id': 17})
    assert waiting.json()['state'] == 'waiting_user' and waiting.json()['task_space_id'] == 17
    assert client.get(progress, params={'persona': 'bob'}).status_code == 404
    assert client.get(progress, params={'persona': 'alice'}).json()['session_id'] == 'setup-session'
    premature = client.post(progress, json={'persona': 'alice', 'state': 'complete'})
    assert premature.json()['state'] == 'waiting_user'
    saved = client.post(endpoint, json={'persona': 'alice', 'values': {
        'CLIENT_ID': 'actual-client', 'CLIENT_SECRET': 'private-provider-secret',
        'REDIRECT_URI': 'http://localhost:7860/api/channels/youtube/auth/callback'}})
    assert saved.status_code == 200 and saved.json()['configured'] is True
    assert saved.json()['connected'] is False
    assert 'private-provider-secret' not in saved.text and 'actual-client' not in saved.text
    assert 'KEEP_VALUE=keep' in env.read_text()
    assert client.post(progress, json={'persona': 'alice', 'state': 'complete'}).json()['state'] == 'configured'
    calls = []
    def provider(request):
        calls.append(request)
        if request.url.path.endswith('/token'):
            assert b'actual-client' in request.content and b'private-provider-secret' in request.content
            return httpx.Response(200, json={'access_token': 'private-access', 'refresh_token': 'private-refresh', 'expires_in': 3600})
        if request.url.path.endswith('/channels'):
            return httpx.Response(200, json={'items': [{'id': 'actual-channel', 'snippet': {'title': 'Alice'}}]})
        return httpx.Response(200, json={'sub': 'actual-channel', 'name': 'Alice'})
    monkeypatch.setattr(channels, 'OAuthService', lambda *args, **kwargs: OAuthService(*args, transport=httpx.MockTransport(provider), **kwargs))
    oauth = client.post('/api/channels/youtube/auth/start', json={'persona': 'alice'})
    assert oauth.status_code == 200 and 'actual-client' in oauth.json()['authorization_url']
    connected = client.get('/api/channels/youtube/auth/callback', params={'state': oauth.json()['state'], 'code': 'real-offline-contract-code'})
    assert connected.status_code == 200, connected.text
    complete = client.post(progress, json={'persona': 'alice', 'state': 'complete'})
    assert complete.json()['state'] == 'connected' and calls
    assert 'private-access' not in complete.text and 'private-refresh' not in complete.text


@pytest.mark.parametrize('values', [
    {'MODEL_KEY': 'not-a-platform-field'}, {'CLIENT_SECRET': 'value\nOTHER_KEY=injected'},
    {'CLIENT_SECRET': '$(touch-danger)'}, {'REDIRECT_URI': 'https://example.com/unrelated'},
    {'REDIRECT_URI': 'javascript:alert(1)'}, {'REDIRECT_URI': 'https://[invalid'},
])
def test_application_config_rejects_invalid_fields_and_keeps_existing_file(setup, values):
    client, env = setup
    original = env.read_bytes()
    response = client.post('/api/channel-applications/youtube', json={'persona': 'alice', 'values': values})
    assert response.status_code == 400, response.text
    assert env.read_bytes() == original


def test_unavailable_agent_only_blocks_setup_not_existing_browser_login(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'api'})
    response = client.post('/api/channel-applications/youtube/setup', json={'persona': 'alice', 'session_id': 'no-agent'})
    assert response.status_code == 409
    accounts = client.get('/api/accounts', params={'persona': 'alice'}).json()
    assert next(row for row in accounts if row['platform'] == 'xiaohongshu')['loggedIn'] is True


def test_application_admin_routes_reject_remote_writes_and_reads(setup):
    _, env = setup
    client = TestClient(web.app, base_url='http://localhost:7860', client=('203.0.113.9', 4000))
    original = env.read_bytes()
    assert client.get('/api/channel-applications/youtube?persona=alice').status_code == 403
    assert client.post('/api/channel-applications/youtube', json={'persona': 'alice', 'values': {'CLIENT_ID': 'bad'}}).status_code == 403
    assert env.read_bytes() == original


def test_validation_errors_hide_secret_inputs(setup):
    client, env = setup
    original = env.read_bytes()
    response = client.post('/api/channel-applications/youtube', json={
        'persona': 'alice', 'values': {'CLIENT_SECRET': {'nested': 'private-invalid-value'}}})
    assert response.status_code == 422
    assert 'private-invalid-value' not in response.text
    assert env.read_bytes() == original


def test_parallel_application_updates_preserve_both_platforms_and_private_permissions(setup):
    client, env = setup
    def save(platform):
        return client.post('/api/channel-applications/' + platform, json={'persona': 'alice', 'values': {'CLIENT_ID': platform, 'CLIENT_SECRET': 'private-' + platform}}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(save, ['youtube', 'twitter'])) == [200, 200]
    text = env.read_text()
    assert 'EASEL_YOUTUBE_CLIENT_ID=youtube' in text and 'EASEL_TWITTER_CLIENT_ID=twitter' in text
    assert 'KEEP_VALUE=keep' in text and env.stat().st_mode & 0o777 == 0o600
