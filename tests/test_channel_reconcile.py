"""Authenticated exact-work recovery after a real provider success loses its receipt."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
import pytest

from easel.channels import Credential
from easel.channels.models import JsonDocument
from easel.content_library import ContentLibrary
from easel.content_library_models import ContentScope, GroupInput, RecordInput
from easel.publish_ledger import PublishLedger
from easel.publish_models import RunState
from easel.publish_recovery import recover_interrupted
from tests.test_channel_publishing import Harness, harness, intent


def unknown_run(harness: Harness, monkeypatch: pytest.MonkeyPatch, platform: str = 'twitter', content: JsonDocument | None = None) -> str:
    payload = harness.approve(platform, content or intent())
    original = PublishLedger.checkpoint_run
    def crash(self, run_id, **kwargs):
        if kwargs.get('platform_work_id'):
            raise sqlite3.OperationalError('crash after provider response')
        return original(self, run_id, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(PublishLedger, 'checkpoint_run', crash)
        with pytest.raises(sqlite3.OperationalError):
            harness.client.post(f'/api/channel-publish/{platform}/publish', json=payload)
    ledger = PublishLedger(harness.root / '_publish/ledger.sqlite3')
    recover_interrupted(ledger)
    return harness.client.post(f'/api/channel-publish/{platform}/publish', json=payload).json()['run_id']


@pytest.mark.parametrize('evidence', ['matching', 'different_text', 'old_post', 'missing_time', 'different_author', 'different_account', 'different_work', 'draft'])
def test_reconciliation_uses_real_account_exact_work_and_content_time(harness: Harness, monkeypatch: pytest.MonkeyPatch, evidence: str):
    run_id = unknown_run(harness, monkeypatch)
    original = harness.provider.handle
    published = datetime.now(timezone.utc).isoformat()
    def provider(request):
        if request.url.path.endswith('/users/me') and evidence == 'different_account':
            return httpx.Response(200, json={'data': {'id': 'someone-else'}})
        if request.url.path.endswith('/tweets/work'):
            row = {'id': 'different' if evidence == 'different_work' else 'work',
                'text': 'unrelated' if evidence == 'different_text' else harness.provider.posts[0]['text'],
                'created_at': None if evidence == 'missing_time' else '2000-01-01T00:00:00Z' if evidence == 'old_post' else published,
                'author_id': 'other' if evidence == 'different_author' else 'actor',
                'status': 'draft' if evidence == 'draft' else 'published'}
            return httpx.Response(200, json={'data': row})
        return original(request)
    monkeypatch.setattr(harness.provider, 'handle', provider)
    # Refreshing the token preserves the original account's authenticated recovery rights.
    harness.store.save(Credential(persona='alice', platform='twitter', account_uid='actor', access_token=SecretStr('fresh-token')))
    response = harness.client.post(f'/api/channel-publish/twitter/{run_id}/reconcile',
        json={'persona': 'alice', 'account_uid': 'actor', 'platform_work_id': 'work'})
    ledger = PublishLedger(harness.root / '_publish/ledger.sqlite3')
    if evidence == 'matching':
        assert response.json()['verification_outcome'] == 'verified'
        assert ledger.get_run(run_id).state == RunState.PUBLISHED
        assert ledger.get_run(run_id).payload['published_at'] == published
        assert (harness.root / '_schedule/alice.json').is_file()
    else:
        assert ledger.get_run(run_id).state == RunState.UNKNOWN
        assert response.status_code == (403 if evidence == 'different_author' else 200)
        assert ledger.pending_effects() == ()
    assert len(harness.provider.posts) == 1


@pytest.mark.parametrize('forged', [{'outcome': 'verified'}, {'details': {'verified': True}}, {'upload_url': 'https://attacker.invalid'}])
def test_reconciliation_rejects_client_asserted_evidence(harness: Harness, monkeypatch: pytest.MonkeyPatch, forged):
    run_id = unknown_run(harness, monkeypatch)
    response = harness.client.post(f'/api/channel-publish/twitter/{run_id}/reconcile',
        json={'persona': 'alice', 'account_uid': 'actor', 'platform_work_id': 'work', **forged})
    assert response.status_code == 422
    assert PublishLedger(harness.root / '_publish/ledger.sqlite3').get_run(run_id).state == RunState.UNKNOWN
    assert len(harness.provider.posts) == 1


@pytest.mark.parametrize('platform,media,options', [
    ('tiktok', 'project/video.mp4', {'privacy_level': 'PUBLIC_TO_EVERYONE'}),
    ('tiktok', 'project/video.mp4', {'privacy_level': 'SELF_ONLY'}),
    ('facebook', 'project/video.mp4', {'content_category': 'reel'}),
    ('pinterest', 'project/image.jpg', {'board_id': 'owned-board'}),
    ('threads', '', {}),
])
def test_official_platforms_complete_only_server_stored_stages(harness: Harness, monkeypatch: pytest.MonkeyPatch, platform: str, media: str, options):
    original = harness.provider.handle
    harness.provider.ready = True
    statuses = ['PROCESSING_UPLOAD', 'PUBLISH_COMPLETE']
    def provider(request):
        path = request.url.path
        if path.endswith('/user/info/'):
            return httpx.Response(200, json={'data': {'user': {'open_id': 'actor'}}})
        if path.endswith('/creator_info/query/'):
            return httpx.Response(200, json={'data': {'privacy_level_options': ['PUBLIC_TO_EVERYONE', 'SELF_ONLY']}})
        if path.endswith('/video/init/'):
            harness.provider.containers.append(json.loads(request.content))
            return httpx.Response(200, json={'data': {'publish_id': 'container', 'upload_url': 'https://open-upload.tiktokapis.com/upload'}})
        if path.endswith('/status/fetch/'):
            status = statuses.pop(0)
            if status == 'PUBLISH_COMPLETE':
                harness.provider.posts.append({'id': 'work'})
            return httpx.Response(200, json={'data': {'status': status,
                'publicly_available_post_id': [] if options.get('privacy_level') == 'SELF_ONLY' else ['work']}})
        if path.endswith('/video/query/'):
            return httpx.Response(200, json={'data': {'videos': [{'id': 'work', 'share_url': 'https://www.tiktok.com/@actor/video/work'}]}})
        if path.endswith('/video_reels'):
            if request.url.params.get('upload_phase') == 'start':
                return httpx.Response(200, json={'video_id': 'container', 'upload_url': 'https://rupload.facebook.com/fb-upload'})
            harness.provider.posts.append(json.loads(request.content))
            return httpx.Response(200, json={'success': True, 'post_id': 'work'})
        if platform == 'facebook' and request.method == 'GET' and path.endswith('/container'):
            return httpx.Response(200, json={'id': 'container', 'status': {'video_status': 'ready'}})
        if platform == 'facebook' and request.method == 'GET' and path.endswith('/work'):
            return httpx.Response(200, json={'id': 'work', 'from': {'id': 'actor'}, 'permalink_url': 'https://www.facebook.com/work'})
        if path.endswith('/fb-upload'):
            harness.provider.uploads.append(request.read())
            return httpx.Response(200, json={'success': True})
        if path.endswith('/user_account'):
            return httpx.Response(200, json={'id': 'actor'})
        if path.endswith('/threads'):
            harness.provider.containers.append(json.loads(request.content))
            return httpx.Response(200, json={'id': 'container'})
        return original(request)
    monkeypatch.setattr(harness.provider, 'handle', provider)
    payload = harness.approve(platform, {**intent(media), 'options': options})
    endpoint = f'/api/channel-publish/{platform}'
    value = harness.client.post(endpoint + '/publish', json=payload).json()
    for _attempt in range(2):
        if value['state'] == 'submitted':
            value = harness.client.post(endpoint + '/' + value['run_id'] + '/finalize', json={'persona': 'alice', 'account_uid': 'actor'}).json()
    assert value['state'] == 'published', value
    assert len(harness.provider.posts) == 1
    if platform in {'tiktok', 'facebook'}:
        assert harness.provider.uploads == [b'approved-video']
    if options.get('privacy_level') == 'SELF_ONLY':
        assert value['visibility'] == 'private' and value['work_link_status'] == 'pending' and value['platform_work_id'] == ''
    if platform == 'pinterest':
        url = urlsplit(harness.provider.posts[0]['media_source']['url'])
        assert harness.client.get(url.path + '?' + url.query).content == b'approved-image'


@pytest.mark.parametrize('approval', ['explicit_private', 'default_private', 'future_schedule'])
def test_youtube_reconciliation_respects_approved_privacy_and_schedule(harness: Harness, monkeypatch: pytest.MonkeyPatch, approval: str):
    original = harness.provider.handle
    scheduled = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 86400, timezone.utc).isoformat()
    options = {} if approval == 'default_private' else {'privacyStatus': 'private'}
    if approval == 'future_schedule':
        options['publishAt'] = scheduled
    def provider(request):
        if request.method == 'GET' and request.url.path.endswith('/videos'):
            return httpx.Response(200, json={'items': [{'id': 'work',
                'snippet': {'channelId': 'actor', 'title': 'A reviewed work', 'publishedAt': datetime.now(timezone.utc).isoformat()},
                'status': {'privacyStatus': 'private', **({'publishAt': scheduled} if approval == 'future_schedule' else {})}}]})
        return original(request)
    monkeypatch.setattr(harness.provider, 'handle', provider)
    run_id = unknown_run(harness, monkeypatch, 'youtube', {**intent('project/video.mp4'), 'options': options})
    response = harness.client.post(f'/api/channel-publish/youtube/{run_id}/reconcile',
        json={'persona': 'alice', 'account_uid': 'actor', 'platform_work_id': 'work'})
    assert response.json()['state'] == ('published' if approval == 'explicit_private' else 'unknown')
    assert len(harness.provider.posts) == 1


def test_fresh_review_resumes_original_frozen_pending_artifact(harness: Harness):
    payload = harness.approve('instagram', intent('project/image.jpg'))
    value = harness.client.post('/api/channel-publish/instagram/publish', json=payload).json()
    endpoint = f"/api/channel-publish/instagram/{value['run_id']}"
    scope = {'persona': 'alice', 'account_uid': 'actor'}
    (harness.root / 'project/image.jpg').write_bytes(b'changed-source-after-submission')
    harness.store.save(Credential(**scope, platform='instagram', access_token=SecretStr('rotated')))
    assert harness.client.post(endpoint + '/finalize', json=scope).status_code == 409
    review = harness.client.post(endpoint + '/reapprove/precheck', json=scope).json()
    assert review['original_intent']['title'] == 'A reviewed work'
    assert harness.client.get(review['frozen_media'][0]['preview_url']).content == b'approved-image'
    approval = {**scope, 'reviewed': True, **{key: review[key] for key in ('approval_digest', 'approval_expires_at')}}
    assert harness.client.post(endpoint + '/reapprove', json=approval).json()['state'] == 'submitted'
    assert harness.client.post(endpoint + '/reapprove', json=approval).status_code == 409
    harness.provider.ready = True
    assert harness.client.post(endpoint + '/finalize', json=scope).json()['state'] == 'published'
    assert len(harness.provider.containers) == len(harness.provider.posts) == 1


@pytest.mark.parametrize('change', ['persona', 'account', 'review', 'credential', 'forged_intent'])
def test_pending_reapproval_keeps_actor_and_review_bound(harness: Harness, change: str):
    value = harness.client.post('/api/channel-publish/instagram/publish', json=harness.approve('instagram', intent('project/image.jpg'))).json()
    endpoint = f"/api/channel-publish/instagram/{value['run_id']}"
    scope = {'persona': 'alice', 'account_uid': 'actor'}
    review = harness.client.post(endpoint + '/reapprove/precheck', json=scope).json()
    payload = {**scope, 'reviewed': True, **{key: review[key] for key in ('approval_digest', 'approval_expires_at')}}
    if change == 'persona': payload['persona'] = 'bob'
    if change == 'account': payload['account_uid'] = 'other'
    if change == 'review': payload['reviewed'] = False
    if change == 'credential':
        harness.store.save(Credential(**scope, platform='instagram', access_token=SecretStr('another-revision')))
    if change == 'forged_intent': payload['original_intent'] = {'title': 'replacement'}
    result = harness.client.post(endpoint + '/reapprove', json=payload)
    assert result.status_code == {'persona': 404, 'account': 404, 'review': 403, 'credential': 409, 'forged_intent': 422}[change]
    assert harness.provider.posts == [] and len(harness.provider.containers) == 1


@pytest.mark.parametrize('owner', ['alice', 'bob'])
def test_publication_context_usage_is_owned_and_replay_safe(harness: Harness, owner: str):
    library = ContentLibrary(harness.root)
    scope = ContentScope(persona=owner, domain='material')
    group = library.create_group(scope, GroupInput(name='Ideas'))
    material = library.create(scope, RecordInput(group_id=group.id, title='Material', kind='text'))
    content = {**intent(), 'context': {'materialId': material.id, 'materialGroupId': group.id}}
    if owner == 'bob':
        response = harness.client.post('/api/channel-publish/twitter/precheck', json=content)
        assert response.status_code == 404
        assert harness.provider.posts == []
    else:
        payload = harness.approve('twitter', content)
        value = harness.client.post('/api/channel-publish/twitter/publish', json=payload).json()
        assert value['state'] == 'published'
        assert harness.client.post('/api/channel-publish/twitter/publish', json=payload).json()['run_id'] == value['run_id']
        assert library.get(scope, material.id).use_count == 1


@pytest.mark.parametrize('timing', ['before_submit', 'between_stages'])
def test_platform_kill_switch_stops_every_provider_stage(harness: Harness, monkeypatch: pytest.MonkeyPatch, timing: str):
    payload = harness.approve('youtube', intent('project/video.mp4'))
    original = harness.provider.handle
    def provider(request):
        if timing == 'before_submit':
            pytest.fail('disabled publishing made an external request')
        result = original(request)
        if request.method == 'POST' and '/upload/youtube/' in request.url.path:
            harness.settings['EASEL_PUBLISH_DISABLED_PLATFORMS'] = ' youtube, twitter '
        return result
    monkeypatch.setattr(harness.provider, 'handle', provider)
    if timing == 'before_submit':
        harness.settings['EASEL_PUBLISH_DISABLED_PLATFORMS'] = 'youtube'
    assert harness.client.post('/api/channel-publish/youtube/publish', json=payload).status_code == 403
    assert harness.provider.uploads == harness.provider.posts == []
    if timing == 'between_stages':
        harness.settings['EASEL_PUBLISH_DISABLED_PLATFORMS'] = ''
        monkeypatch.setattr(harness.provider, 'handle', original)
        value = harness.client.post('/api/channel-publish/youtube/publish', json=payload).json()
        response = harness.client.post(f"/api/channel-publish/youtube/{value['run_id']}/finalize", json={'persona': 'alice', 'account_uid': 'actor'})
        assert response.json()['state'] == 'published' and harness.provider.uploads == [b'approved-video']
