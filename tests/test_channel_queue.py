"""Scheduled jobs dispatch the reviewed frozen snapshot through the normal publisher."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
import time

import httpx
from pydantic import SecretStr
import pytest

from easel._channel_publish_models import ChannelPublishRequest
from easel._channel_publish_queue import PublicationQueue
from easel.channel_publishing import ChannelPublishing
from easel.channels import ChannelAdapter, ChannelError, Credential
from easel.publish_models import IdempotencyConflict, LeaseBusy, RunState
from tests.test_channel_publishing import Harness, harness, intent


def service(harness: Harness) -> ChannelPublishing:
    return ChannelPublishing(harness.root, lambda: harness.settings,
        lambda platform: ChannelAdapter(platform, transport=httpx.MockTransport(harness.provider.handle)))


def scheduled(harness: Harness) -> tuple[ChannelPublishRequest, float]:
    stamp = datetime.now(timezone.utc) + timedelta(hours=1)
    payload = harness.approve('youtube', {**intent('project/video.mp4'), 'scheduled_for': stamp.isoformat()})
    return ChannelPublishRequest.model_validate(payload), stamp.timestamp()


def test_prepared_queue_uses_approved_bytes_after_source_edit_and_waits_until_due(harness: Harness, monkeypatch: pytest.MonkeyPatch):
    publisher = service(harness)
    request, due = scheduled(harness)
    queued = publisher.prepare_task('youtube', request)
    assert queued.state == 'queued'
    assert harness.provider.posts == harness.provider.uploads == []
    (harness.root / 'project/video.mp4').write_bytes(b'edited-source')
    (harness.root / 'project/.easel.json').write_text('{"profile":"bob"}')
    assert publisher.prepare_task('youtube', request).run_id == queued.run_id
    with pytest.raises(ChannelError, match='scheduled_publish_not_due'):
        publisher.publish('youtube', request)
    assert harness.provider.posts == []
    monkeypatch.setattr(time, 'time', lambda: due + 1)
    result = service(harness).publish('youtube', request)
    assert result.state == 'published' and result.run_id == queued.run_id
    assert harness.provider.uploads == [b'approved-video']
    assert len(harness.provider.posts) == 1


def test_prepared_queue_freezes_standalone_cover_before_dispatch(harness: Harness, monkeypatch: pytest.MonkeyPatch):
    (harness.root / 'project/cover.jpg').write_bytes(b'approved-cover')
    stamp = datetime.now(timezone.utc) + timedelta(hours=1)
    payload = harness.approve('youtube', {**intent('project/video.mp4'), 'cover_path': 'project/cover.jpg', 'scheduled_for': stamp.isoformat()})
    request = ChannelPublishRequest.model_validate(payload)
    original = harness.provider.handle
    thumbnails: list[bytes] = []
    def provider(request):
        if request.url.path.endswith('/thumbnails/set'):
            thumbnails.append(request.read())
            return httpx.Response(200, json={'items': [{'default': {'url': 'https://i.ytimg.com/work.jpg'}}]})
        return original(request)
    monkeypatch.setattr(harness.provider, 'handle', provider)
    publisher = service(harness)
    queued = publisher.prepare_task('youtube', request)
    (harness.root / 'project/video.mp4').unlink()
    (harness.root / 'project/cover.jpg').write_bytes(b'edited-cover')
    monkeypatch.setattr(time, 'time', lambda: stamp.timestamp() + 1)
    result = publisher.publish('youtube', request)
    assert result.state == 'published' and result.cover_status == 'applied' and result.run_id == queued.run_id
    assert thumbnails == [b'approved-cover'] and harness.provider.uploads == [b'approved-video']


@pytest.mark.parametrize('change', ['credential', 'policy', 'approval', 'intent', 'frozen_bytes'])
def test_queued_dispatch_rechecks_authorization_and_frozen_integrity(harness: Harness, monkeypatch: pytest.MonkeyPatch, change: str):
    publisher = service(harness)
    request, due = scheduled(harness)
    queued = publisher.prepare_task('youtube', request)
    if change == 'credential':
        harness.store.save(Credential(persona='alice', platform='youtube', account_uid='actor', access_token=SecretStr('rotated')))
    if change == 'policy': harness.settings['EASEL_PUBLISH_DISABLED_PLATFORMS'] = 'youtube'
    if change == 'approval': request = request.model_copy(update={'approval_digest': '0' * 64})
    if change == 'intent': request = request.model_copy(update={'title': 'edited-client-title'})
    if change == 'frozen_bytes':
        path = harness.root / '_publish/alice' / queued.run_id / 'media/00.mp4'
        path.chmod(0o600)
        path.write_bytes(b'corrupt-frozen-video')
    monkeypatch.setattr(time, 'time', lambda: due + 1)
    with pytest.raises((ChannelError, IdempotencyConflict)):
        publisher.publish('youtube', request)
    assert publisher.ledger.get_run(queued.run_id).state == RunState.QUEUED
    assert harness.provider.posts == harness.provider.uploads == []


def test_concurrent_queue_preparation_cannot_replace_an_active_freeze(harness: Harness, monkeypatch: pytest.MonkeyPatch):
    publisher = service(harness)
    request, _due = scheduled(harness)
    entered, release = Event(), Event()
    freeze = PublicationQueue.freeze
    def paused(self, run, prepared, lease):
        entered.set()
        assert release.wait(5)
        return freeze(self, run, prepared, lease)
    monkeypatch.setattr(PublicationQueue, 'freeze', paused)
    with ThreadPoolExecutor(max_workers=1) as worker:
        first = worker.submit(publisher.prepare_task, 'youtube', request)
        assert entered.wait(5)
        try:
            with pytest.raises(LeaseBusy):
                service(harness).prepare_task('youtube', request)
        finally:
            release.set()
        value = first.result()
    runs = publisher.ledger.list_runs(persona='alice')
    assert len(runs) == 1 and runs[0].run_id == value.run_id and runs[0].state == RunState.QUEUED
    assert harness.provider.posts == harness.provider.uploads == []


def test_queued_rotation_review_uses_frozen_media_and_keeps_original_dispatch(harness: Harness, monkeypatch: pytest.MonkeyPatch):
    (harness.root / 'project/cover.jpg').write_bytes(b'approved-cover')
    stamp = datetime.now(timezone.utc) + timedelta(hours=1)
    request = ChannelPublishRequest.model_validate(harness.approve('youtube', {
        **intent('project/video.mp4'), 'cover_path': 'project/cover.jpg', 'scheduled_for': stamp.isoformat()}))
    publisher = service(harness)
    queued = publisher.prepare_task('youtube', request)
    (harness.root / 'project/video.mp4').unlink()
    (harness.root / 'project/cover.jpg').unlink()
    harness.store.save(Credential(persona='alice', platform='youtube', account_uid='actor', access_token=SecretStr('rotated')))
    endpoint = f'/api/channel-publish/youtube/{queued.run_id}'
    scope = {'persona': 'alice', 'account_uid': 'actor'}
    review = harness.client.post(endpoint + '/reapprove/precheck', json=scope)
    assert review.status_code == 200, review.text
    approved = review.json()
    assert approved['original_intent']['scheduled_for'] == request.scheduled_for
    assert [harness.client.get(item['preview_url']).content for item in approved['frozen_media']] == [b'approved-video', b'approved-cover']
    renewed = {**scope, 'reviewed': True, **{key: approved[key] for key in ('approval_digest', 'approval_expires_at')}}
    assert harness.client.post(endpoint + '/reapprove', json=renewed).json()['state'] == 'queued'
    assert harness.client.post(endpoint + '/reapprove', json=renewed).json()['state'] == 'queued'
    assert publisher.prepare_task('youtube', request).run_id == queued.run_id
    assert harness.provider.posts == harness.provider.uploads == []
    with pytest.raises(ChannelError, match='scheduled_publish_not_due'):
        publisher.publish('youtube', request)
    original = harness.provider.handle
    covers: list[bytes] = []
    def provider(request):
        if request.url.path.endswith('/thumbnails/set'):
            covers.append(request.read())
            return httpx.Response(200, json={'items': [{'default': {'url': 'https://i.ytimg.com/work.jpg'}}]})
        return original(request)
    monkeypatch.setattr(harness.provider, 'handle', provider)
    monkeypatch.setattr(time, 'time', lambda: stamp.timestamp() + 1)
    completed = service(harness).publish('youtube', request)
    assert completed.state == 'published' and completed.run_id == queued.run_id
    assert service(harness).publish('youtube', request).run_id == queued.run_id
    assert harness.provider.uploads == [b'approved-video'] and covers == [b'approved-cover']
    assert len(harness.provider.posts) == 1


@pytest.mark.parametrize('change', ['persona', 'account', 'review', 'credential', 'policy'])
def test_queued_rotation_review_is_scoped_and_invalidated(harness: Harness, change: str):
    publisher = service(harness)
    request, _due = scheduled(harness)
    queued = publisher.prepare_task('youtube', request)
    endpoint = f'/api/channel-publish/youtube/{queued.run_id}'
    scope = {'persona': 'alice', 'account_uid': 'actor'}
    review = harness.client.post(endpoint + '/reapprove/precheck', json=scope).json()
    body = {**scope, 'reviewed': True, **{key: review[key] for key in ('approval_digest', 'approval_expires_at')}}
    if change == 'persona': body['persona'] = 'bob'
    if change == 'account': body['account_uid'] = 'other'
    if change == 'review': body['reviewed'] = False
    if change == 'credential': harness.store.save(Credential(**scope, platform='youtube', access_token=SecretStr('rotated-again')))
    if change == 'policy': harness.settings['EASEL_PUBLISH_DISABLED_PLATFORMS'] = 'youtube'
    response = harness.client.post(endpoint + '/reapprove', json=body)
    assert response.status_code == {'persona': 404, 'account': 404, 'review': 403, 'credential': 409, 'policy': 403}[change]
    assert publisher.ledger.get_run(queued.run_id).state == RunState.QUEUED
    assert harness.provider.posts == harness.provider.uploads == []
