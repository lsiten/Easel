from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from easel.ownership import OwnershipError, owned_media_path
from easel.upload_sessions import UploadSessions
from easel.upload_sessions_models import UploadAccess, UploadReceipt
from easel.content_library_models import ContentError
from easel.upload_sessions_router import UploadPolicy, create_router


@pytest.fixture
def uploads(tmp_path):
    accepted = []
    def accept(completed):
        session = completed.session
        project = tmp_path / ('upload-' + session.id)
        project.mkdir(exist_ok=True)
        (project / '.easel.json').write_text(json.dumps({'profile': session.persona}))
        target = project / session.filename
        shutil.copyfile(completed.path, target)
        accepted.append(session.id)
        return UploadReceipt(id=session.id, name=session.filename, path=target.relative_to(tmp_path).as_posix())
    def write(request: Request):
        if request.headers.get('x-write') != 'yes':
            raise HTTPException(403)
    app = FastAPI()
    policy = UploadPolicy(max_bytes=8, extensions=frozenset({'.png'}), accept_upload=accept)
    app.include_router(create_router(lambda: tmp_path, lambda persona: persona in {'alice', 'bob'}, write, policy=policy))
    with TestClient(app, headers={'x-write': 'yes'}) as client:
        yield tmp_path, client, accepted, accept


def _issue(client, *, persona='alice', public=False, data=b'image', **overrides):
    return client.post('/api/asset-uploads/sessions', json={
        'persona': persona, 'session_id': 'chat-1', 'filename': 'photo.png',
        'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'public': public, **overrides})


def test_signed_public_upload_confirm_callback_and_owned_content(uploads):
    root, client, accepted, _ = uploads
    grant = _issue(client, public=True).json()
    client.headers.pop('x-write')
    assert client.put(grant['upload_url'], content=b'image').json()['state'] == 'uploaded'
    confirmed = client.post(grant['confirm_url'])
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()
    assert (root / receipt['path']).read_bytes() == b'image'
    assert owned_media_path(root, 'alice', receipt['path'])[1].is_file()
    with pytest.raises(OwnershipError):
        owned_media_path(root, 'bob', receipt['path'])
    callback = grant['confirm_url'].replace('/confirm?', '/callback?')
    assert client.post(callback).json() == receipt
    assert client.post(grant['confirm_url']).json() == receipt
    assert accepted == [grant['id']]
    assert client.get('/api/asset-uploads/' + grant['id'], params={'persona': 'bob'}).status_code == 404


def test_issuance_limits_token_scope_and_private_auth(uploads):
    _, client, _, _ = uploads
    assert _issue(client, size=9).status_code == 413
    assert _issue(client, filename='../photo.png').status_code == 400
    assert _issue(client, filename='script.py').status_code == 400
    first, second = _issue(client, public=True).json(), _issue(client, public=True).json()
    bad = first['upload_url'].replace(first['token'], second['token'])
    assert client.put(bad, content=b'image').status_code == 403
    private = _issue(client).json()
    client.headers.pop('x-write')
    assert client.put(private['upload_url'], content=b'image').status_code == 403
    assert _issue(client, public=True).status_code == 403
    assert client.post(f'/api/asset-uploads/public/{private["id"]}/confirm',
                       params={'token': private['token']}).status_code == 403


def test_integrity_caps_failed_receive_retry_and_expiry(uploads, monkeypatch):
    _, client, accepted, _ = uploads
    grant = _issue(client, public=True).json()
    assert client.put(grant['upload_url'], content=b'123456').status_code == 413
    assert client.post(grant['confirm_url']).status_code == 409
    assert client.put(grant['upload_url'], content=b'wrong').status_code == 409
    assert client.put(grant['upload_url'], content=b'image').status_code == 200
    import easel.upload_sessions_store as store
    monkeypatch.setattr(store.time, 'time', lambda: grant['expires_at'] + 1)
    assert client.post(grant['confirm_url']).status_code == 410
    assert accepted == []


def test_concurrent_confirm_is_idempotent_and_tampered_stage_is_rejected(uploads):
    root, client, accepted, accept = uploads
    grant = _issue(client, public=True).json()
    assert client.put(grant['upload_url'], content=b'image').status_code == 200
    access = UploadAccess(token=grant['token'])
    def confirm(_):
        store = UploadSessions(root, max_bytes=8, extensions=frozenset({'.png'}))
        return store.confirm(grant['id'], access, accept).path
    with ThreadPoolExecutor(max_workers=5) as workers:
        assert len(set(workers.map(confirm, range(12)))) == 1
    assert accepted == [grant['id']]
    other = _issue(client, public=True).json()
    assert client.put(other['upload_url'], content=b'image').status_code == 200
    (root / '_upload_sessions' / 'data' / other['id']).write_bytes(b'wrong')
    assert client.post(other['confirm_url']).status_code == 409


def test_callback_failure_keeps_verified_upload_retryable(uploads):
    root, client, _, accept = uploads
    grant = _issue(client, public=True).json()
    assert client.put(grant['upload_url'], content=b'image').status_code == 200
    store = UploadSessions(root, max_bytes=8, extensions=frozenset({'.png'}))
    access = UploadAccess(token=grant['token'])
    def failing(_completed):
        raise OSError('disk full')
    with pytest.raises(OSError, match='disk full'):
        store.confirm(grant['id'], access, failing)
    assert store.confirm(grant['id'], access, accept).name == 'photo.png'


def test_dead_upload_receiver_is_reclaimed_after_process_exit(uploads):
    root, client, _, _ = uploads
    grant = _issue(client, public=True).json()
    child = '''
import os,sys,anyio
from pathlib import Path
from easel.upload_sessions import UploadSessions
from easel.upload_sessions_models import UploadAccess
store=UploadSessions(Path(sys.argv[1]),max_bytes=8,extensions=frozenset({'.png'}))
async def chunks():
    yield b'im'
    os._exit(0)
anyio.run(store.receive,sys.argv[2],UploadAccess(persona='alice'),chunks())
'''
    subprocess.run([sys.executable, '-c', child, str(root), grant['id']], check=True, timeout=15)
    assert len(list((root / '_upload_sessions/data').iterdir())) == 1
    assert client.put(grant['upload_url'], content=b'image').status_code == 200
    assert len(list((root / '_upload_sessions/data').iterdir())) == 1
    assert client.post(grant['confirm_url']).status_code == 200


def test_live_attempt_blocks_reclaim_and_expired_attempt_cannot_reset_successor(uploads, monkeypatch):
    import anyio
    import easel.upload_sessions_store as storage
    root, client, _, _ = uploads
    grant = _issue(client, public=True).json()
    store = UploadSessions(root, max_bytes=8, extensions=frozenset({'.png'}))
    access = UploadAccess(token=grant['token'])
    first = store.begin(grant['id'], access)
    with pytest.raises(ContentError, match='upload_state_conflict'):
        UploadSessions(root, max_bytes=8, extensions=frozenset({'.png'})).begin(grant['id'], access)
    monkeypatch.setattr(storage.time, 'time', lambda: first.attempt_expires_at + 1)
    second = store.begin(grant['id'], access)
    assert second.attempt_id != first.attempt_id
    monkeypatch.undo()
    # Resume the old receiver after a newer fenced attempt has claimed the grant.
    monkeypatch.setattr(store, 'begin', lambda identifier, supplied_access: first)
    async def chunks():
        yield b'image'
    with pytest.raises(ContentError, match='upload_state_conflict'):
        anyio.run(store.receive, grant['id'], access, chunks())
    assert store.status(grant['id'], access).attempt_id == second.attempt_id
    assert store.status(grant['id'], access).state == 'uploading'


@pytest.mark.parametrize('target', ['attachment', 'media'])
def test_signed_upload_target_tampering_is_rejected(uploads, target):
    root, client, accepted, _ = uploads
    grant = _issue(client, public=True, target=target).json()
    store = UploadSessions(root, max_bytes=8, extensions=frozenset({'.png'}))
    session = store.status(grant['id'], UploadAccess(persona='alice'))
    altered = session.model_copy(update={'target': 'media' if target == 'attachment' else 'attachment'})
    with store.connection() as db:
        store._save(db, altered)
    assert client.put(grant['upload_url'], content=b'image').status_code == 403
    assert accepted == []


def test_existing_attachment_grant_without_target_remains_usable(uploads):
    root, client, _, _ = uploads
    grant = _issue(client, public=True).json()
    store = UploadSessions(root, max_bytes=8, extensions=frozenset({'.png'}))
    with store.connection() as db:
        row = db.execute('SELECT body FROM upload_sessions WHERE id=?', (grant['id'],)).fetchone()
        legacy = json.loads(row[0])
        legacy.pop('target')
        db.execute('UPDATE upload_sessions SET body=? WHERE id=?', (json.dumps(legacy), grant['id']))
    assert client.put(grant['upload_url'], content=b'image').status_code == 200
    assert client.post(grant['confirm_url']).status_code == 200
    assert store.status(grant['id'], UploadAccess(persona='alice')).target == 'attachment'


@pytest.fixture
def root_uploads(tmp_path, monkeypatch):
    from web import app as web
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    monkeypatch.setattr(web, 'profile_exists', lambda persona: persona in {'alice', 'bob'})
    return tmp_path, TestClient(web.app, base_url='http://localhost:7860', headers={'Origin': 'http://localhost:7860'})


@pytest.mark.parametrize('target', ['media', 'attachment'])
def test_root_upload_confirmation_can_feed_owned_media_library(root_uploads, target):
    root, client = root_uploads
    grant = _issue(client, public=True, target=target).json()
    assert client.put(grant['upload_url'], content=b'image').status_code == 200
    confirmed = client.post(grant['confirm_url'])
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()
    assert (root / receipt['path']).read_bytes() == b'image'
    assert client.post(grant['confirm_url']).json() == receipt
    group = client.post('/api/content/media/groups?persona=alice', json={'name': 'Uploads'}).json()
    record = client.post('/api/content/media/records?persona=alice', json={
        'group_id': group['id'], 'title': 'Uploaded media', 'kind': 'image', 'path': receipt['path']})
    if target == 'media':
        assert record.status_code == 200, record.text
        assert owned_media_path(root, 'alice', receipt['path'])[1].read_bytes() == b'image'
        assert client.get('/api/content/media/records/' + record.json()['id'], params={'persona': 'bob'}).status_code == 404
    else:
        assert receipt['path'].startswith('_inbox/') and record.status_code == 403


def test_media_upload_preserves_existing_foreign_project_owner(root_uploads):
    root, client = root_uploads
    grant = _issue(client, public=True, target='media').json()
    project = root / ('upload-' + grant['id'])
    project.mkdir()
    (project / '.easel.json').write_text('{"profile":"bob"}')
    assert client.put(grant['upload_url'], content=b'image').status_code == 200
    assert client.post(grant['confirm_url']).status_code == 403
    assert json.loads((project / '.easel.json').read_text())['profile'] == 'bob'
    assert not (project / 'photo.png').exists()
