"""Native edits operate on a real published task and preserve its publication identity."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
import pytest

from easel.channels import ChannelAdapter, Credential
from easel.channels.models import JsonDocument
from easel.publish_ledger import PublishLedger
from easel.publish_tasks import FlowItem, FlowRequest, TaskStore
from easel.publish_update_router import create_router
from tests.test_channel_publishing import Harness, harness, intent


class WorkServer:
    """Mutable native provider document with conditional edits and thumbnail replacement."""
    def __init__(self, base, platform: str) -> None:
        self.base = base
        self.platform = platform
        self.work: JsonDocument = {}
        self.edits: list[JsonDocument] = []
        self.thumbnails: list[bytes] = []
        self.race = False
        self.thumbnail_error = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith('/user_account'):
            return httpx.Response(200, json={'id': 'actor'})
        if path.endswith('/pins') and request.method == 'POST':
            self.work = {**json.loads(request.content), 'id': 'work'}
        if path.endswith('/pins') and request.method == 'GET':
            return httpx.Response(200, json={'items': [self.work]})
        if path.endswith('/pins/work'):
            if request.method == 'PATCH':
                edit = json.loads(request.content)
                self.edits.append(edit)
                self.work = {**self.work, **edit}
            return httpx.Response(200, json=self.work)
        if request.method == 'POST' and path.endswith('/upload/youtube/v3/videos'):
            self.work = {**json.loads(request.content), 'id': 'work', 'etag': 'version-1'}
            self.work['snippet']['channelId'] = 'actor'
        if request.method == 'GET' and path.endswith('/videos'):
            return httpx.Response(200, json={'items': [self.work]})
        if request.method == 'PUT' and path.endswith('/videos'):
            if self.race:
                self.work['etag'] = 'other-edit'
            if request.headers.get('if-match') and request.headers['if-match'] != '"' + self.work['etag'] + '"':
                return httpx.Response(412)
            update = json.loads(request.content)
            assert update['id'] == 'work'
            self.edits.append(update)
            self.work = {**update, 'etag': 'version-' + str(len(self.edits) + 1)}
            self.work['snippet']['channelId'] = 'actor'
            return httpx.Response(200, json=self.work)
        if path.endswith('/thumbnails/set'):
            assert request.url.params['videoId'] == 'work'
            if self.thumbnail_error:
                return httpx.Response(500)
            self.thumbnails.append(request.read())
            return httpx.Response(200, json={'items': [{'default': {'url': 'https://i.ytimg.com/work.jpg'}}]})
        return self.base(request)


@dataclass(frozen=True, slots=True)
class PublishedTask:
    harness: Harness
    server: WorkServer
    client: TestClient
    task_id: str
    run_id: str

    def approve(self, cover: bool = False) -> JsonDocument:
        intent: JsonDocument = {'persona': 'alice', 'account_uid': 'actor', 'title': 'Updated title', 'body': 'Updated description'}
        if cover:
            (self.harness.root / 'project/cover.jpg').write_bytes(b'approved-new-cover')
            intent['cover_path'] = 'project/cover.jpg'
        response = self.client.post(f'/api/publish-flows/tasks/{self.task_id}/update/precheck', json=intent)
        assert response.status_code == 200, response.text
        value = response.json()
        assert (value['current']['snippet']['title'] if self.server.platform == 'youtube' else value['current']['title']) == 'A reviewed work'
        return {**intent, 'reviewed': True, 'request_key': 'update-1', **{key: value[key]
            for key in ('approval_digest', 'approval_expires_at', 'viewed_revision')}}


@pytest.fixture
def published(harness: Harness, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> PublishedTask:
    platform = getattr(request, 'param', 'youtube')
    server = WorkServer(harness.provider.handle, platform)
    monkeypatch.setattr(harness.provider, 'handle', server.handle)
    stamp = datetime.now(timezone.utc) - timedelta(seconds=1)
    content = {**intent('project/video.mp4' if platform == 'youtube' else 'project/image.jpg'), 'scheduled_for': stamp.isoformat()}
    if platform == 'pinterest': content['options'] = {'board_id': 'board'}
    reviewed = harness.approve(platform, content)
    store = TaskStore(harness.root / '_publish')
    store.create(FlowRequest(persona='alice', flow_id='editable-task', publish_at=stamp, content=content, reviewed=True,
        items=(FlowItem(platform=platform, account_uid='actor', approval_digest=reviewed['approval_digest'], approval_expires_at=reviewed['approval_expires_at']),)))
    task_id, platform, payload = store.claim('test-worker', time.time())
    response = harness.client.post(f'/api/channel-publish/{platform}/publish', json={key: value for key, value in payload.items() if key != 'run_id'})
    assert response.status_code == 200 and response.json()['state'] == 'published', response.text
    store.finish(task_id, 'test-worker', 'published', response.json())
    def guard(request: Request) -> None:
        if request.headers.get('x-deny') == '1': raise HTTPException(403)
    app = FastAPI()
    app.include_router(create_router(lambda: harness.root, lambda: harness.settings, lambda p: p in {'alice', 'bob'}, guard,
        adapter_factory=lambda p: ChannelAdapter(p, transport=httpx.MockTransport(server.handle))))
    return PublishedTask(harness, server, TestClient(app), task_id, response.json()['run_id'])


@pytest.mark.parametrize('cover', [False, True])
def test_reviewed_native_update_is_durable_replay_safe_and_refreshes_projection(published: PublishedTask, cover: bool):
    payload = published.approve(cover)
    endpoint = f'/api/publish-flows/tasks/{published.task_id}/update'
    response = published.client.post(endpoint, json=payload)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['state'] == 'succeeded' and value['metadata_applied'] and value['platform_work_id'] == 'work'
    assert published.client.post(endpoint, json=payload).json()['update_id'] == value['update_id']
    assert published.server.work['snippet']['title'] == 'Updated title'
    assert len(published.server.edits) == len(published.harness.provider.posts) == 1
    assert published.server.thumbnails == ([b'approved-new-cover'] if cover else [])
    ledger = PublishLedger(published.harness.root / '_publish/ledger.sqlite3')
    run = ledger.get_run(published.run_id)
    assert run.payload['title'] == 'Updated title' and run.payload['channel_intent']['title'] == 'A reviewed work'
    rows = json.loads((published.harness.root / '_schedule/alice.json').read_text())
    assert len(rows) == 1 and rows[0]['title'] == 'Updated title'


@pytest.mark.parametrize('change', ['review', 'persona', 'account', 'content', 'credential', 'current', 'cover', 'forged_target', 'guard'])
def test_task_update_keeps_review_actor_and_server_target_bound(published: PublishedTask, change: str):
    payload = published.approve(cover=change == 'cover')
    if change == 'review': payload['reviewed'] = False
    if change == 'persona': payload['persona'] = 'bob'
    if change == 'account': payload['account_uid'] = 'other'
    if change == 'content': payload['title'] = 'Different approved content'
    if change == 'credential':
        published.harness.store.save(Credential(persona='alice', platform='youtube', account_uid='actor', access_token=SecretStr('rotated')))
    if change == 'current': published.server.work['snippet']['title'] = 'Remote newer edit'
    if change == 'cover': (published.harness.root / 'project/cover.jpg').write_bytes(b'replaced-cover')
    if change == 'forged_target': payload['platform_work_id'] = 'unrelated-work'
    response = published.client.post(f'/api/publish-flows/tasks/{published.task_id}/update', json=payload,
        headers={'x-deny': '1'} if change == 'guard' else {})
    assert response.status_code == {'review': 403, 'persona': 404, 'account': 404, 'content': 409,
        'credential': 409, 'current': 409, 'cover': 409, 'forged_target': 422, 'guard': 403}[change]
    assert published.server.edits == [] and len(published.harness.provider.posts) == 1


def test_external_etag_change_prevents_overwriting_a_concurrent_native_edit(published: PublishedTask):
    payload = published.approve()
    published.server.race = True
    response = published.client.post(f'/api/publish-flows/tasks/{published.task_id}/update', json=payload)
    assert response.json()['state'] == 'unknown' and response.json()['error'] == 'work_revision_changed'
    assert published.server.edits == [] and len(published.harness.provider.posts) == 1


@pytest.mark.parametrize('phase', ['metadata', 'cover'])
def test_interrupted_update_acknowledgement_never_recreates_the_publication(published: PublishedTask, monkeypatch: pytest.MonkeyPatch, phase: str):
    payload = published.approve(cover=phase == 'cover')
    endpoint = f'/api/publish-flows/tasks/{published.task_id}/update'
    original = PublishLedger.checkpoint_run
    def crash(self, run_id, **kwargs):
        records = kwargs.get('payload', {}).get('publication_updates', {}).get('records', {}).values()
        if any(record['metadata_applied'] and (phase == 'metadata' or record['cover_status'] == 'applied') for record in records):
            raise sqlite3.OperationalError('update acknowledgement interrupted')
        return original(self, run_id, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(PublishLedger, 'checkpoint_run', crash)
        with pytest.raises(sqlite3.OperationalError): published.client.post(endpoint, json=payload)
    if phase == 'cover': (published.harness.root / 'project/cover.jpg').write_bytes(b'changed-after-first-cover-set')
    replay = published.client.post(endpoint, json=payload).json()
    assert replay['state'] == ('unknown' if phase == 'metadata' else 'succeeded')
    assert len(published.server.edits) == len(published.harness.provider.posts) == 1
    assert published.server.thumbnails == ([] if phase == 'metadata' else [b'approved-new-cover', b'approved-new-cover'])


def test_thumbnail_failure_retains_the_successful_native_metadata_edit(published: PublishedTask):
    payload = published.approve(cover=True)
    published.server.thumbnail_error = True
    value = published.client.post(f'/api/publish-flows/tasks/{published.task_id}/update', json=payload).json()
    assert value['state'] == 'succeeded' and value['cover_status'] == 'failed'
    assert published.server.work['snippet']['title'] == 'Updated title'
    assert len(published.server.edits) == len(published.harness.provider.posts) == 1


@pytest.mark.parametrize('published', ['pinterest'], indirect=True)
def test_pinterest_native_patch_updates_the_original_owned_pin(published: PublishedTask):
    payload = published.approve()
    endpoint = f'/api/publish-flows/tasks/{published.task_id}/update'
    response = published.client.post(endpoint, json=payload)
    assert response.status_code == 200 and response.json()['state'] == 'succeeded', response.text
    assert published.server.work['id'] == 'work' and published.server.work['title'] == 'Updated title'
    assert len(published.server.edits) == len(published.harness.provider.posts) == 1
