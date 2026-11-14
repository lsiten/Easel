"""Real HTTP, provider adapters, and persisted ledger publication contracts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
import pytest

from easel.channel_publish_router import create_router
from easel.channels import ChannelAdapter, Credential, CredentialStore
from easel.channels.models import JsonDocument
from easel.publish_ledger import PublishLedger
from easel.publish_models import RunState
from easel.publish_recovery import recover_interrupted


class Provider:
    """Mutable offline platform state resolves uploads and actual created works."""

    def __init__(self) -> None:
        self.posts: list[JsonDocument] = []
        self.containers: list[JsonDocument] = []
        self.uploads: list[bytes] = []
        self.ready = False
        self.reject = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith('/users/me'):
            return httpx.Response(200, json={'data': {'id': 'actor'}})
        if path.endswith('/me') or path.endswith('/userinfo'):
            return httpx.Response(200, json={'id': 'actor', 'sub': 'actor'})
        if path.endswith('/channels'):
            return httpx.Response(200, json={'items': [{'id': 'actor'}]})
        if request.method == 'PUT':
            self.uploads.append(request.read())
            if 'youtube' in path:
                self.posts.append({'id': 'work', 'bytes': self.uploads[-1].hex()})
                return httpx.Response(200, json={'id': 'work'})
            return httpx.Response(200, headers={'etag': 'part-1'})
        if path.endswith('/videos') and request.method == 'GET':
            return httpx.Response(200, json={'items': [{'id': 'work', 'snippet': {'channelId': 'actor'}}]})
        if path.endswith('/videos') and 'upload' in path:
            return httpx.Response(200, headers={'location': 'https://www.googleapis.com/youtube-session?secret=server-only'})
        if path.endswith('/images') and request.method == 'POST':
            return httpx.Response(200, json={'value': {'image': 'urn:li:image:one', 'uploadUrl': 'https://media.licdn.com/upload'}})
        if '/images/' in path:
            return httpx.Response(200, json={'status': 'AVAILABLE' if self.ready else 'WAITING_UPLOAD'})
        if path.endswith('/media'):
            self.containers.append(json.loads(request.content))
            return httpx.Response(200, json={'id': 'container'})
        if path.endswith('/container'):
            return httpx.Response(200, json={'status_code': 'ERROR' if self.reject else 'FINISHED' if self.ready else 'IN_PROGRESS'})
        if path.endswith('/work'):
            return httpx.Response(200, json={'id': 'work', 'permalink': 'https://www.instagram.com/p/work/'})
        if request.method == 'POST':
            self.posts.append(json.loads(request.content))
            if path.endswith('/tweets'):
                return httpx.Response(201, json={'data': {'id': 'work'}})
            if path.endswith('/posts'):
                return httpx.Response(201, headers={'x-restli-id': 'urn:li:share:work'})
            return httpx.Response(200, json={'id': 'work'})
        pytest.fail(f'unexpected provider endpoint {request.method} {request.url}')


@dataclass(frozen=True, slots=True)
class Harness:
    root: Path
    provider: Provider
    client: TestClient
    store: CredentialStore
    settings: dict[str, str]

    def approve(self, platform: str, intent: JsonDocument) -> JsonDocument:
        response = self.client.post(f'/api/channel-publish/{platform}/precheck', json=intent)
        assert response.status_code == 200, response.text
        return {**intent, **{key: response.json()[key] for key in ('approval_digest', 'approval_expires_at')},
                'reviewed': True, 'idempotency_key': 'intent-1'}


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    project = tmp_path / 'project'
    project.mkdir()
    (project / '.easel.json').write_text('{"profile":"alice"}')
    (project / 'image.jpg').write_bytes(b'approved-image')
    (project / 'video.mp4').write_bytes(b'approved-video')
    store = CredentialStore(tmp_path / '_channels')
    for platform in ('twitter', 'instagram', 'youtube', 'linkedin', 'threads', 'facebook', 'pinterest', 'tiktok'):
        store.save(Credential(persona='alice', platform=platform, account_uid='actor', access_token=SecretStr('token-1'), parent_uid='parent'))
    provider, settings = Provider(), {'EASEL_CHANNEL_MEDIA_BASE_URL': 'https://content.example'}
    def guard(request: Request) -> None:
        if request.headers.get('x-deny') == '1':
            raise HTTPException(403, 'denied')
    app = FastAPI()
    app.include_router(create_router(lambda: tmp_path, lambda: settings, lambda p: p in {'alice', 'bob'}, guard,
        adapter_factory=lambda platform: ChannelAdapter(platform, transport=httpx.MockTransport(provider.handle))))
    return Harness(tmp_path, provider, TestClient(app), store, settings)


def intent(media: str = '') -> JsonDocument:
    return {'persona': 'alice', 'account_uid': 'actor', 'title': 'A reviewed work', 'body': 'Body',
            'media': [{'path': media, 'kind': 'video' if media.endswith('.mp4') else 'image'}] if media else []}


@pytest.mark.parametrize('platform,media', [('twitter', ''), ('youtube', 'project/video.mp4')])
def test_success_is_durable_idempotent_and_enqueues_projection(harness: Harness, platform: str, media: str):
    payload = harness.approve(platform, intent(media))
    first = harness.client.post(f'/api/channel-publish/{platform}/publish', json=payload)
    assert first.status_code == 200, first.text
    value = first.json()
    assert value['state'] == 'published'
    replay = harness.client.post(f'/api/channel-publish/{platform}/publish', json=payload)
    assert replay.json()['run_id'] == value['run_id']
    ledger = PublishLedger(harness.root / '_publish/ledger.sqlite3')
    assert len(harness.provider.posts) == 1
    assert ledger.get_run(value['run_id']).platform_work_id == 'work'
    assert json.loads((harness.root / '_schedule/alice.json').read_text())[0]['run_id'] == value['run_id']
    assert ledger.pending_effects() == ()
    assert 'server-only' not in first.text
    if media:
        assert harness.provider.uploads == [b'approved-video']


def test_pending_survives_restart_and_uses_frozen_owned_bytes(harness: Harness):
    payload = harness.approve('instagram', intent('project/image.jpg'))
    value = harness.client.post('/api/channel-publish/instagram/publish', json=payload).json()
    assert value['state'] == 'submitted'
    ledger = PublishLedger(harness.root / '_publish/ledger.sqlite3')
    assert recover_interrupted(ledger) == ()
    (harness.root / 'project/image.jpg').write_bytes(b'changed-original')
    url = urlsplit(harness.provider.containers[0]['image_url'])
    assert harness.client.get(url.path + '?' + url.query).content == b'approved-image'
    scope = {'persona': 'alice', 'account_uid': 'actor'}
    waiting = harness.client.post(f"/api/channel-publish/instagram/{value['run_id']}/finalize", json=scope)
    assert waiting.json()['state'] == 'submitted'
    harness.provider.ready = True
    completed = harness.client.post(f"/api/channel-publish/instagram/{value['run_id']}/finalize", json=scope)
    assert completed.json()['state'] == 'published'
    assert len(harness.provider.containers) == len(harness.provider.posts) == 1
    assert harness.client.get(url.path + '?' + url.query.replace('signature=', 'signature=x')).status_code == 403


def test_uploaded_media_resumes_processing_before_creating_post(harness: Harness):
    payload = harness.approve('linkedin', intent('project/image.jpg'))
    value = harness.client.post('/api/channel-publish/linkedin/publish', json=payload).json()
    assert value['state'] == 'submitted' and harness.provider.posts == []
    harness.provider.ready = True
    completed = harness.client.post(f"/api/channel-publish/linkedin/{value['run_id']}/finalize", json={'persona': 'alice', 'account_uid': 'actor'})
    assert completed.json()['state'] == 'published'
    assert harness.provider.uploads == [b'approved-image']
    assert harness.provider.posts[0]['content']['media']['id'] == 'urn:li:image:one'


@pytest.mark.parametrize('change', ['review', 'content', 'credential', 'media', 'persona', 'mode', 'expired', 'forged_receipt'])
def test_approval_and_ownership_boundaries_prevent_submission(harness: Harness, change: str):
    payload = harness.approve('instagram', intent('project/image.jpg'))
    if change == 'review': payload['reviewed'] = False
    if change == 'content': payload['title'] = 'Changed title'
    if change == 'credential':
        harness.store.save(Credential(persona='alice', platform='instagram', account_uid='actor', access_token=SecretStr('rotated')))
    if change == 'media': (harness.root / 'project/image.jpg').write_bytes(b'changed')
    if change == 'persona':
        harness.store.save(Credential(persona='bob', platform='instagram', account_uid='actor', access_token=SecretStr('token-1')))
        payload['persona'] = 'bob'
    if change == 'mode': harness.settings['EASEL_PUBLISH_MODE'] = 'manual'
    if change == 'expired': payload['approval_expires_at'] = int(time.time()) - 1
    if change == 'forged_receipt': payload['upload_url'] = 'https://attacker.invalid/upload'
    response = harness.client.post('/api/channel-publish/instagram/publish', json=payload)
    assert response.status_code in {403, 409, 422}
    assert harness.provider.posts == harness.provider.containers == []


def test_account_scope_and_forged_finalize_receipt_are_rejected(harness: Harness):
    payload = harness.approve('instagram', intent('project/image.jpg'))
    value = harness.client.post('/api/channel-publish/instagram/publish', json=payload).json()
    endpoint = f"/api/channel-publish/instagram/{value['run_id']}"
    assert harness.client.get(endpoint, params={'persona': 'bob', 'account_uid': 'actor'}).status_code == 404
    assert harness.client.post(endpoint + '/finalize', json={'persona': 'alice', 'account_uid': 'other'}).status_code == 404
    assert harness.client.post(endpoint + '/finalize', json={'persona': 'alice', 'account_uid': 'actor', 'receipt': {'state': 'published'}}).status_code == 422
    assert harness.client.post(endpoint + '/finalize', json={'persona': 'alice', 'account_uid': 'actor'}, headers={'x-deny': '1'}).status_code == 403
    assert harness.provider.posts == []


def test_lost_receipt_after_remote_success_never_resubmits(harness: Harness, monkeypatch: pytest.MonkeyPatch):
    payload = harness.approve('twitter', intent())
    original = PublishLedger.checkpoint_run
    def crash(self, run_id, **kwargs):
        if kwargs.get('target') == RunState.PUBLISHED:
            raise sqlite3.OperationalError('simulated crash after remote success')
        return original(self, run_id, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(PublishLedger, 'checkpoint_run', crash)
        with pytest.raises(sqlite3.OperationalError):
            harness.client.post('/api/channel-publish/twitter/publish', json=payload)
    replay = harness.client.post('/api/channel-publish/twitter/publish', json=payload)
    assert replay.json()['state'] == 'unknown'
    assert len(harness.provider.posts) == 1
    assert PublishLedger(harness.root / '_publish/ledger.sqlite3').pending_effects() == ()


def test_concurrent_requests_fence_same_intent(harness: Harness, monkeypatch: pytest.MonkeyPatch):
    payload = harness.approve('twitter', intent())
    entered, resume = Event(), Event()
    original = harness.provider.handle
    def paused(request):
        if request.url.path.endswith('/tweets'):
            entered.set()
            assert resume.wait(3)
        return original(request)
    monkeypatch.setattr(harness.provider, 'handle', paused)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(harness.client.post, '/api/channel-publish/twitter/publish', json=payload)
        assert entered.wait(3)
        second = harness.client.post('/api/channel-publish/twitter/publish', json=payload)
        resume.set()
        assert first.result().json()['state'] == 'published'
    assert second.status_code == 409
    assert len(harness.provider.posts) == 1


def test_explicit_provider_failure_can_retry_with_review(harness: Harness):
    payload = harness.approve('instagram', intent('project/image.jpg'))
    value = harness.client.post('/api/channel-publish/instagram/publish', json=payload).json()
    harness.provider.reject = True
    endpoint = f"/api/channel-publish/instagram/{value['run_id']}/finalize"
    scope = {'persona': 'alice', 'account_uid': 'actor'}
    assert harness.client.post(endpoint, json=scope).json()['state'] == 'failed'
    harness.provider.reject, harness.provider.ready = False, True
    retry = {**harness.approve('instagram', intent('project/image.jpg')), 'retry': True}
    assert harness.client.post('/api/channel-publish/instagram/publish', json=retry).json()['run_id'] == value['run_id']
    assert harness.client.post(endpoint, json=scope).json()['state'] == 'published'
    assert len(harness.provider.containers) == 2 and len(harness.provider.posts) == 1


def test_scheduled_approval_remains_bound_to_due_time(harness: Harness):
    scheduled = datetime.fromtimestamp(time.time() + 86400, timezone.utc).isoformat()
    payload = harness.approve('twitter', {**intent(), 'scheduled_for': scheduled})
    assert payload['approval_expires_at'] > time.time() + 86400
    assert harness.client.post('/api/channel-publish/twitter/publish', json=payload).status_code == 409
    payload['scheduled_for'] = ''
    assert harness.client.post('/api/channel-publish/twitter/publish', json=payload).status_code == 409
    assert harness.provider.posts == []


def test_stale_worker_cannot_commit_provider_response(harness: Harness, monkeypatch: pytest.MonkeyPatch):
    payload = harness.approve('twitter', intent())
    original = harness.provider.handle
    def supersede(request):
        result = original(request)
        if request.url.path.endswith('/tweets'):
            ledger = PublishLedger(harness.root / '_publish/ledger.sqlite3')
            with ledger._connect() as db:
                db.execute('UPDATE publish_leases SET fencing_token=fencing_token+1')
        return result
    monkeypatch.setattr(harness.provider, 'handle', supersede)
    response = harness.client.post('/api/channel-publish/twitter/publish', json=payload)
    assert response.status_code == 409
    ledger = PublishLedger(harness.root / '_publish/ledger.sqlite3')
    assert ledger.list_runs(persona='alice')[0].state == RunState.RUNNING
    assert ledger.pending_effects() == ()
    assert len(harness.provider.posts) == 1
