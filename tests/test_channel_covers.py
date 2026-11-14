"""Owned covers stay outside primary media and resume without recreating a video."""
from __future__ import annotations

import json
import sqlite3
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
import pytest
from PIL import Image

from easel.channels import Credential
from easel.publish_ledger import PublishLedger
from easel.publish_models import RunState
from easel.publish_recovery import recover_interrupted
from tests.test_channel_publishing import Harness, harness, intent


@pytest.fixture
def covered(harness: Harness) -> Harness:
    (harness.root / 'project/cover.jpg').write_bytes(b'approved-cover')
    return harness


@pytest.mark.parametrize('outcome', ['success', 'provider_error', 'receipt_crash'])
def test_youtube_thumbnail_preserves_created_video_identity(covered: Harness, monkeypatch: pytest.MonkeyPatch, outcome: str):
    original = covered.provider.handle
    thumbnails: dict[str, bytes] = {}
    calls: list[str] = []
    ledger = PublishLedger(covered.root / '_publish/ledger.sqlite3')
    def provider(request):
        if request.url.path.endswith('/thumbnails/set'):
            run = ledger.list_runs(persona='alice')[0]
            assert run.platform_work_id == 'work'
            assert run.payload['channel_progress']['stage'] == 'publication_cover'
            assert run.payload['published_at']
            calls.append(request.url.params['videoId'])
            if outcome == 'provider_error':
                return httpx.Response(500)
            thumbnails[request.url.params['videoId']] = request.read()
            return httpx.Response(200, json={'items': [{'default': {'url': 'https://i.ytimg.com/work.jpg'}}]})
        return original(request)
    monkeypatch.setattr(covered.provider, 'handle', provider)
    payload = covered.approve('youtube', {**intent('project/video.mp4'), 'cover_path': 'project/cover.jpg'})
    checkpoint = PublishLedger.checkpoint_run
    def crash(self, run_id, **kwargs):
        if kwargs.get('target') == RunState.PUBLISHED:
            raise sqlite3.OperationalError('thumbnail acknowledgement interrupted')
        return checkpoint(self, run_id, **kwargs)
    if outcome == 'receipt_crash':
        with monkeypatch.context() as patch:
            patch.setattr(PublishLedger, 'checkpoint_run', crash)
            with pytest.raises(sqlite3.OperationalError):
                covered.client.post('/api/channel-publish/youtube/publish', json=payload)
        run = ledger.list_runs(persona='alice')[0]
        recover_interrupted(ledger)
        (covered.root / 'project/cover.jpg').write_bytes(b'changed-after-video-created')
        value = covered.client.post(f'/api/channel-publish/youtube/{run.run_id}/finalize',
            json={'persona': 'alice', 'account_uid': 'actor'}).json()
    else:
        response = covered.client.post('/api/channel-publish/youtube/publish', json=payload)
        assert response.status_code == 200, response.text
        value = response.json()
        assert covered.client.post('/api/channel-publish/youtube/publish', json=payload).json()['run_id'] == value['run_id']
    assert value['state'] == 'published' and value['platform_work_id'] == 'work'
    assert value['cover_status'] == ('failed' if outcome == 'provider_error' else 'applied')
    assert len(covered.provider.posts) == 1
    assert calls == (['work', 'work'] if outcome == 'receipt_crash' else ['work'])
    assert thumbnails == ({} if outcome == 'provider_error' else {'work': b'approved-cover'})


def test_instagram_cover_is_frozen_separately_and_reapproval_keeps_it(covered: Harness):
    payload = covered.approve('instagram', {**intent('project/video.mp4'), 'options': {'cover_url': 'project/cover.jpg'}})
    value = covered.client.post('/api/channel-publish/instagram/publish', json=payload).json()
    assert value['state'] == 'submitted'
    body = covered.provider.containers[0]
    cover_url = urlsplit(body['cover_url'])
    assert body['media_type'] == 'REELS' and 'children' not in body
    assert covered.client.get(cover_url.path + '?' + cover_url.query).content == b'approved-cover'
    (covered.root / 'project/cover.jpg').write_bytes(b'edited-source-cover')
    scope = {'persona': 'alice', 'account_uid': 'actor'}
    covered.store.save(Credential(**scope, platform='instagram', access_token=SecretStr('fresh')))
    endpoint = f"/api/channel-publish/instagram/{value['run_id']}"
    review = covered.client.post(endpoint + '/reapprove/precheck', json=scope).json()
    previews = review['frozen_media']
    assert [item['role'] for item in previews] == ['media', 'cover']
    assert covered.client.get(previews[1]['preview_url']).content == b'approved-cover'
    covered.provider.ready = True
    result = covered.client.post(endpoint + '/reapprove', json={**scope, 'reviewed': True,
        **{key: review[key] for key in ('approval_digest', 'approval_expires_at')}})
    assert result.json()['state'] == 'published'
    assert len(covered.provider.containers) == len(covered.provider.posts) == 1


def test_pinterest_cover_alias_becomes_signed_owned_url_without_extra_media(covered: Harness, monkeypatch: pytest.MonkeyPatch):
    original = covered.provider.handle
    def provider(request):
        if request.url.path.endswith('/user_account'):
            return httpx.Response(200, json={'id': 'actor'})
        if request.url.path.endswith('/media') and request.method == 'POST':
            return httpx.Response(200, json={'media_id': 'uploaded-video', 'upload_url': 'https://bucket.s3.amazonaws.com/upload', 'upload_parameters': {}})
        if request.url.host == 'bucket.s3.amazonaws.com':
            covered.provider.uploads.append(request.read())
            return httpx.Response(204)
        if request.url.path.endswith('/media/uploaded-video'):
            return httpx.Response(200, json={'status': 'succeeded'})
        return original(request)
    monkeypatch.setattr(covered.provider, 'handle', provider)
    payload = covered.approve('pinterest', {**intent('project/video.mp4'),
        'options': {'board_id': 'board', 'cover_image_url': 'project/cover.jpg'}})
    response = covered.client.post('/api/channel-publish/pinterest/publish', json=payload)
    assert response.json()['state'] == 'published', response.text
    source = covered.provider.posts[0]['media_source']
    assert source['source_type'] == 'video_id' and source['media_id'] == 'uploaded-video'
    url = urlsplit(source['cover_image_url'])
    assert covered.client.get(url.path + '?' + url.query).content == b'approved-cover'
    assert b'approved-video' in covered.provider.uploads[0]


@pytest.mark.parametrize('change', ['source', 'foreign_owner', 'unsupported_platform', 'wrong_format', 'alias_conflict'])
def test_cover_review_rejects_changes_ownership_and_unsupported_modes(covered: Harness, change: str):
    content = {**intent('project/video.mp4'), 'cover_path': 'project/cover.jpg'}
    if change == 'unsupported_platform':
        assert covered.client.post('/api/channel-publish/twitter/precheck', json=content).status_code == 400
        return
    if change == 'wrong_format':
        content['cover_path'] = 'project/video.mp4'
        assert covered.client.post('/api/channel-publish/youtube/precheck', json=content).status_code == 400
        return
    if change == 'alias_conflict':
        content['options'] = {'cover_url': 'project/image.jpg'}
        assert covered.client.post('/api/channel-publish/instagram/precheck', json=content).status_code == 400
        return
    payload = covered.approve('youtube', content)
    if change == 'source':
        (covered.root / 'project/cover.jpg').write_bytes(b'replaced-before-submit')
    if change == 'foreign_owner':
        (covered.root / 'project/.easel.json').write_text(json.dumps({'profile': 'bob'}))
    response = covered.client.post('/api/channel-publish/youtube/publish', json=payload)
    assert response.status_code == (409 if change == 'source' else 403)
    assert covered.provider.posts == covered.provider.uploads == []


@pytest.mark.parametrize('explicit_index', [None, 0])
def test_tiktok_cover_selects_only_a_published_photo(covered: Harness, monkeypatch: pytest.MonkeyPatch, explicit_index):
    original = covered.provider.handle
    def provider(request):
        path = request.url.path
        if path.endswith('/user/info/'):
            return httpx.Response(200, json={'data': {'user': {'open_id': 'actor'}}})
        if path.endswith('/creator_info/query/'):
            return httpx.Response(200, json={'data': {'privacy_level_options': ['PUBLIC_TO_EVERYONE'], 'creator_username': 'actor'}})
        if path.endswith('/content/init/'):
            covered.provider.containers.append(json.loads(request.content))
            return httpx.Response(200, json={'data': {'publish_id': 'photo-job'}})
        if path.endswith('/status/fetch/'):
            covered.provider.posts.append({'id': 'photo-work'})
            return httpx.Response(200, json={'data': {'status': 'PUBLISH_COMPLETE', 'publicly_available_post_id': ['photo-work']}})
        return original(request)
    monkeypatch.setattr(covered.provider, 'handle', provider)
    options = {'privacy_level': 'PUBLIC_TO_EVERYONE'}
    if explicit_index is not None:
        options['photo_cover_index'] = explicit_index
    content = {**intent('project/image.jpg'), 'cover_path': 'project/cover.jpg', 'options': options,
        'media': [{'path': 'project/image.jpg', 'kind': 'image'}, {'path': 'project/cover.jpg', 'kind': 'image'}]}
    payload = covered.approve('tiktok', content)
    value = covered.client.post('/api/channel-publish/tiktok/publish', json=payload).json()
    source = covered.provider.containers[0]['source_info']
    assert len(source['photo_images']) == 2 and source['photo_cover_index'] == (1 if explicit_index is None else explicit_index)
    response = covered.client.post(f"/api/channel-publish/tiktok/{value['run_id']}/finalize", json={'persona': 'alice', 'account_uid': 'actor'})
    assert response.json()['state'] == 'published'
    assert response.json()['permalink'] == 'https://www.tiktok.com/@actor/photo/photo-work'


@pytest.mark.parametrize('video', [True, False])
def test_tiktok_rejects_cover_sources_outside_its_photo_selection(covered: Harness, video: bool):
    content = {**intent('project/video.mp4' if video else 'project/image.jpg'),
        'cover_path': 'project/cover.jpg', 'options': {'privacy_level': 'PUBLIC_TO_EVERYONE'}}
    response = covered.client.post('/api/channel-publish/tiktok/precheck', json=content)
    assert response.status_code == 400 and covered.provider.posts == []


def test_instagram_accepts_portrait_reels_cover_metadata(covered: Harness):
    with Image.new('RGB', (420, 654), 'white') as image:
        image.save(covered.root / 'project/cover.jpg')
    response = covered.client.post('/api/channel-publish/instagram/precheck',
        json={**intent('project/video.mp4'), 'cover_path': 'project/cover.jpg'})
    assert response.status_code == 200, response.text
    assert covered.provider.containers == []


def test_precheck_retains_gif_format_when_validating_mixed_twitter_media(covered: Harness):
    with Image.new('RGB', (2, 2), 'white') as image:
        image.save(covered.root / 'project/animation.gif')
    content = {**intent(), 'media': [{'kind': 'image', 'path': 'project/animation.gif'}, {'kind': 'image', 'path': 'project/image.jpg'}]}
    response = covered.client.post('/api/channel-publish/twitter/precheck', json=content)
    assert response.status_code == 400, response.text
    assert covered.provider.posts == []
