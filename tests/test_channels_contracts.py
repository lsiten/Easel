"""Offline provider request and completion contracts, without developer credentials."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from easel.channels import ChannelAdapter, ChannelError, Credential, CredentialStore, PublishContent, PublishReceipt
from easel.channels.http import ProviderHttp
from easel.channels.models import MediaInput
from easel.channels.router import create_router
from easel.channels.webhooks import verify_webhook, webhook_challenge


def credential(platform: str) -> Credential:
    return Credential(persona='alice', platform=platform, account_uid='own-account', access_token=SecretStr('secret-token'))


@pytest.mark.parametrize('platform', ['google-business', 'linkedin'])
def test_unsupported_capabilities_fail_before_network(platform):
    def forbidden(request):
        pytest.fail(f'unexpected provider call {request.url}')
    adapter = ChannelAdapter(platform, transport=httpx.MockTransport(forbidden))
    with pytest.raises(ChannelError, match='works_unsupported'):
        adapter.works(credential(platform))
    with pytest.raises(ChannelError, match='analytics_unsupported'):
        adapter.analytics(credential(platform))
    if platform == 'google-business':
        with pytest.raises(ChannelError, match='publish_unsupported'):
            adapter.publish(credential(platform), PublishContent(body='Hello'))


def test_twitter_publish_uses_actor_token_and_exact_work_identity():
    def provider(request):
        assert request.method == 'POST' and request.url.path == '/2/tweets'
        assert request.headers['authorization'] == 'Bearer secret-token'
        assert json.loads(request.content) == {'text': 'Hello', 'media': {'media_ids': ['uploaded-1']}}
        return httpx.Response(201, json={'data': {'id': 'post-42'}})
    adapter = ChannelAdapter('twitter', transport=httpx.MockTransport(provider))
    receipt = adapter.publish(credential('twitter'), PublishContent(body='Hello', media=(MediaInput(kind='image', media_id='uploaded-1'),)))
    assert receipt.state == 'published'
    assert receipt.platform_work_id == 'post-42'
    assert receipt.permalink == 'https://x.com/i/status/post-42'


def test_linkedin_created_identity_can_arrive_only_in_header():
    def provider(request):
        assert request.headers['linkedin-version'] == '202605'
        assert json.loads(request.content)['author'] == 'urn:li:person:own-account'
        return httpx.Response(201, headers={'x-restli-id': 'urn:li:share:42'})
    result = ChannelAdapter('linkedin', transport=httpx.MockTransport(provider)).publish(credential('linkedin'), PublishContent(body='Hello'))
    assert result.platform_work_id == 'urn:li:share:42'


def test_facebook_user_token_requires_provider_selected_page():
    def forbidden(request): pytest.fail('unselected user must never publish')
    with pytest.raises(ChannelError, match='page_account_required'):
        ChannelAdapter('facebook', transport=httpx.MockTransport(forbidden)).publish(credential('facebook'), PublishContent(body='Hi'))


@pytest.mark.parametrize('platform,edge', [('instagram', 'media'), ('threads', 'threads')])
def test_container_publication_waits_for_processing_before_finalize(platform, edge):
    requests = []
    status = ['IN_PROGRESS', 'FINISHED']
    def provider(request):
        requests.append((request.method, request.url.path))
        if request.method == 'POST' and request.url.path.endswith('/' + edge):
            assert json.loads(request.content)['image_url'] == 'https://assets.example/image.jpg'
            return httpx.Response(200, json={'id': 'container-1'})
        if request.method == 'GET' and request.url.path.endswith('/container-1'):
            return httpx.Response(200, json={'status_code' if platform == 'instagram' else 'status': status.pop(0)})
        if request.method == 'POST':
            assert json.loads(request.content) == {'creation_id': 'container-1'}
            return httpx.Response(200, json={'id': 'work-1'})
        return httpx.Response(200, json={'id': 'work-1', 'permalink': 'https://social.example/one'})
    adapter = ChannelAdapter(platform, transport=httpx.MockTransport(provider))
    pending = adapter.publish(credential(platform), PublishContent(body='Hello', media=(MediaInput(kind='image', url='https://assets.example/image.jpg'),)))
    assert pending.state == 'pending' and not pending.platform_work_id
    waiting = adapter.finalize(credential(platform), pending)
    assert waiting.state == 'pending'
    assert len([method for method, _ in requests if method == 'POST']) == 1
    completed = adapter.finalize(credential(platform), waiting)
    assert completed.state == 'published' and completed.platform_work_id == 'work-1'


def test_tiktok_requires_current_creator_privacy_and_exact_readback():
    def provider(request):
        if request.url.path.endswith('/creator_info/query/'):
            return httpx.Response(200, json={'data': {'privacy_level_options': ['SELF_ONLY']}, 'error': {'code': 'ok'}})
        if request.url.path.endswith('/video/init/'):
            body = json.loads(request.content)
            assert body['post_info']['privacy_level'] == 'SELF_ONLY'
            assert body['source_info'] == {'source': 'PULL_FROM_URL', 'video_url': 'https://assets.example/clip.mp4'}
            return httpx.Response(200, json={'data': {'publish_id': 'job-1'}})
        if request.url.path.endswith('/status/fetch/'):
            return httpx.Response(200, json={'data': {'status': 'PUBLISH_COMPLETE', 'publicaly_available_post_id': ['work-1']}})
        assert json.loads(request.content)['filters']['video_ids'] == ['work-1']
        return httpx.Response(200, json={'data': {'videos': [{'id': 'work-1', 'share_url': 'https://www.tiktok.com/@me/video/work-1'}]}})
    adapter = ChannelAdapter('tiktok', transport=httpx.MockTransport(provider))
    content = PublishContent(body='Hi', media=(MediaInput(kind='video', url='https://assets.example/clip.mp4'),), options={'privacy_level': 'PUBLIC_TO_EVERYONE'})
    with pytest.raises(ChannelError, match='privacy_level_not_allowed'):
        adapter.publish(credential('tiktok'), content)
    receipt = adapter.publish(credential('tiktok'), content.model_copy(update={'options': {'privacy_level': 'SELF_ONLY'}}))
    assert receipt.state == 'pending' and receipt.external_request_id == 'job-1'
    assert adapter.finalize(credential('tiktok'), receipt).platform_work_id == 'work-1'


def test_youtube_upload_session_is_streamed_and_read_back(tmp_path):
    media = tmp_path / 'clip.mp4'
    media.write_bytes(b'video-content')
    def provider(request):
        if request.method == 'POST':
            assert request.url.params['uploadType'] == 'resumable'
            assert json.loads(request.content)['status']['privacyStatus'] == 'public'
            return httpx.Response(200, headers={'Location': 'https://www.googleapis.com/upload/session?id=1'})
        if request.method == 'PUT':
            assert request.content == b'video-content'
            assert request.headers['content-length'] == '13'
            return httpx.Response(200, json={'id': 'video-1'})
        return httpx.Response(200, json={'items': [{'id': 'video-1', 'snippet': {'channelId': 'own-account'}}]})
    adapter = ChannelAdapter('youtube', transport=httpx.MockTransport(provider))
    receipt = adapter.publish(credential('youtube'), PublishContent(title='Video', media=(MediaInput(kind='video'),), options={'content_length': 13}))
    assert receipt.state == 'requires_upload' and not receipt.platform_work_id
    assert adapter.upload(credential('youtube'), receipt, media).platform_work_id == 'video-1'


@pytest.mark.parametrize('platform,payload', [('youtube', {'items': [{'id': 'foreign', 'snippet': {'channelId': 'other'}}]}), ('twitter', {'data': {'id': 'foreign', 'author_id': 'other'}})])
def test_foreign_work_is_rejected_before_edit(platform, payload):
    methods = []
    def provider(request):
        methods.append(request.method)
        return httpx.Response(200, json=payload)
    adapter = ChannelAdapter(platform, transport=httpx.MockTransport(provider))
    with pytest.raises(ChannelError, match='work_owner_mismatch'):
        adapter.delete(credential(platform), 'foreign')
    assert methods == ['GET']


def test_graph_foreign_work_requires_authenticated_owner_or_list_membership():
    def provider(request):
        if request.url.path.endswith('/foreign'): return httpx.Response(200, json={'id': 'foreign'})
        assert request.url.path.endswith('/own-account/media')
        return httpx.Response(200, json={'data': [{'id': 'own-work'}]})
    adapter = ChannelAdapter('instagram', transport=httpx.MockTransport(provider))
    with pytest.raises(ChannelError, match='work_owner_unverified'):
        adapter.work(credential('instagram'), 'foreign')


def test_provider_http_rejects_redirects_errors_and_token_endpoint_escape():
    calls = []
    def provider(request):
        calls.append(request.url)
        return httpx.Response(302, headers={'Location': 'https://attacker.example/token'})
    http = ProviderHttp('twitter', transport=httpx.MockTransport(provider))
    with pytest.raises(ChannelError, match='provider_http_error'):
        http.send('GET', 'https://api.x.com/2/users/me', token='secret')
    with pytest.raises(ChannelError, match='endpoint_not_allowed'):
        http.send('GET', 'https://attacker.example/token', token='secret')
    with pytest.raises(ChannelError, match='endpoint_not_allowed'):
        http.send('GET', 'https://graph.facebook.com/v25.0/me', token='secret')
    assert len(calls) == 1


def test_router_errors_keep_typed_status_and_credentials_private(tmp_path):
    app = FastAPI()
    def write_guard(request): raise HTTPException(403, 'write blocked')
    app.include_router(create_router(lambda: tmp_path, lambda: {}, lambda name: name == 'alice', write_guard))
    store = CredentialStore(tmp_path / '_channels')
    store.save(credential('twitter'))
    with TestClient(app) as client:
        missing = client.get('/api/channels/twitter/profile', params={'persona': 'alice', 'account_uid': 'missing'})
        assert missing.status_code == 404
        assert missing.json()['detail'] == {'platform': 'twitter', 'code': 'account_not_connected'}
        values = client.get('/api/channels/twitter/accounts', params={'persona': 'alice'})
        assert values.status_code == 200 and 'secret-token' not in values.text and 'access_token' not in values.text
        rejected = client.post('/api/channels/twitter/auth/start', json={'persona': 'alice'})
        assert rejected.status_code == 403


@pytest.mark.parametrize('platform,header,algorithm,prefix', [('facebook', 'X-Hub-Signature-256', 'sha256', 'sha256='), ('instagram', 'X-Hub-Signature-256', 'sha256', 'sha256='), ('youtube', 'X-Hub-Signature', 'sha1', 'sha1='), ('linkedin', 'X-Li-Signature', 'sha256', '')])
def test_webhook_signatures_bind_original_bytes(platform, header, algorithm, prefix):
    body = b'{"event":"published"}'
    signature = prefix + hmac.new(b'secret', body, algorithm).hexdigest()
    assert verify_webhook(platform, body, {header: signature}, 'secret')
    assert not verify_webhook(platform, body + b' ', {header: signature}, 'secret')


def test_tiktok_webhook_rejects_expired_timestamp():
    body = b'{"event":"publish_complete"}'
    digest = hmac.new(b'secret', b'1000.' + body, hashlib.sha256).hexdigest()
    headers = {'tiktok-signature': 't=1000,s=' + digest}
    assert verify_webhook('tiktok', body, headers, 'secret', now=1000)
    assert not verify_webhook('tiktok', body, headers, 'secret', now=1301)
    with pytest.raises(ChannelError, match='webhook_verification_failed'):
        webhook_challenge('facebook', {'hub.challenge': 'x', 'hub.mode': 'subscribe'}, 'secret')


def test_tiktok_file_upload_preserves_byte_range_and_pending_state(tmp_path):
    media = tmp_path / 'clip.mp4'
    media.write_bytes(b'video-content')
    def provider(request):
        if request.url.path.endswith('/creator_info/query/'):
            return httpx.Response(200, json={'data': {'privacy_level_options': ['SELF_ONLY']}})
        if request.url.path.endswith('/video/init/'):
            source = json.loads(request.content)['source_info']
            assert source == {'source': 'FILE_UPLOAD', 'video_size': 13, 'chunk_size': 13, 'total_chunk_count': 1}
            return httpx.Response(200, json={'data': {'publish_id': 'upload-1', 'upload_url': 'https://open-upload.tiktokapis.com/session/one'}})
        assert request.method == 'PUT' and request.content == b'video-content'
        assert request.headers['content-range'] == 'bytes 0-12/13'
        assert 'authorization' not in request.headers
        return httpx.Response(201)
    adapter = ChannelAdapter('tiktok', transport=httpx.MockTransport(provider))
    pending = adapter.publish(credential('tiktok'), PublishContent(body='Video', media=(MediaInput(kind='video'),),
        options={'source': 'FILE_UPLOAD', 'content_length': 13, 'privacy_level': 'SELF_ONLY'}))
    assert pending.state == 'requires_upload'
    assert adapter.upload(credential('tiktok'), pending, media).state == 'pending'


def test_work_link_resolution_uses_authenticated_readback():
    def provider(request):
        assert request.url.path == '/2/tweets/12345'
        return httpx.Response(200, json={'data': {'id': '12345', 'author_id': 'own-account'}})
    adapter = ChannelAdapter('twitter', transport=httpx.MockTransport(provider))
    result = adapter.resolve_link(credential('twitter'), 'https://x.com/me/status/12345?tracking=one')
    assert result['platform_work_id'] == '12345'
    assert result['permalink'] == 'https://x.com/me/status/12345'
    with pytest.raises(ChannelError, match='work_link_invalid'):
        adapter.resolve_link(credential('twitter'), 'https://attacker.example/status/12345')


def test_youtube_scheduled_upload_keeps_final_identity_pending_until_release(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from easel.channels import publish as publishing
    future = datetime.now(timezone.utc) + timedelta(days=1)
    privacy = ['private']
    media = tmp_path / 'clip.mp4'
    media.write_bytes(b'video-content')
    def provider(request):
        if request.method == 'PUT': return httpx.Response(200, json={'id': 'video-1'})
        return httpx.Response(200, json={'items': [{'id': 'video-1', 'snippet': {'channelId': 'own-account'},
            'status': {'privacyStatus': privacy[0], **({'publishAt': future.isoformat()} if privacy[0] == 'private' else {})}}]})
    adapter = ChannelAdapter('youtube', transport=httpx.MockTransport(provider))
    original = PublishReceipt(state='requires_upload', external_request_id='server-session', upload_url='https://www.googleapis.com/upload/session',
        details={'privacy_status': 'private', 'publish_at': future.isoformat()})
    receipt = adapter.upload(credential('youtube'), original, media)
    assert receipt.state == 'pending' and receipt.platform_work_id == 'video-1'
    privacy[0] = 'public'
    assert adapter.finalize(credential('youtube'), receipt).state == 'pending'
    class ReleasedClock(datetime):
        @classmethod
        def now(cls, tz=None): return future + timedelta(seconds=1)
    monkeypatch.setattr(publishing, 'datetime', ReleasedClock)
    released = adapter.finalize(credential('youtube'), receipt)
    assert released.state == 'published' and released.platform_work_id == 'video-1'


def test_facebook_reel_requires_explicit_finish_confirmation(tmp_path):
    media = tmp_path / 'clip.mp4'
    media.write_bytes(b'video-content')
    def provider(request):
        if request.url.params.get('upload_phase') == 'start':
            return httpx.Response(200, json={'video_id': 'reel-1', 'upload_url': 'https://rupload.facebook.com/session/1'})
        if request.url.host == 'rupload.facebook.com':
            assert request.headers['authorization'] == 'OAuth secret-token'
            assert request.content == b'video-content'
            return httpx.Response(200, json={'success': True})
        assert json.loads(request.content)['upload_phase'] == 'finish'
        return httpx.Response(200, json={})
    adapter = ChannelAdapter('facebook', transport=httpx.MockTransport(provider))
    page = credential('facebook').model_copy(update={'parent_uid': 'own-user'})
    staged = adapter.publish(page, PublishContent(body='Reel', media=(MediaInput(kind='video', url='https://assets.example/clip.mp4'),), options={'content_category': 'reel'}))
    assert staged.state == 'requires_upload'
    with pytest.raises(ChannelError, match='upload_finalize_failed'):
        adapter.upload(page, staged, media)
