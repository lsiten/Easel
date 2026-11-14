from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from easel.content_library import ContentLibrary
from easel.content_library_models import ContentScope, UsageInput
from easel.content_library_router import create_router


@pytest.fixture
def library(tmp_path):
    for persona in ('alice', 'bob'):
        project = tmp_path / (persona + '-project')
        project.mkdir()
        (project / '.easel.json').write_text(json.dumps({'profile': persona}))
        (project / 'photo.png').write_bytes(b'image')
        (project / 'video.mp4').write_bytes(b'video')
    app = FastAPI()
    def write(request: Request):
        if request.headers.get('x-write') != 'allowed':
            raise HTTPException(403, 'write_denied')
    app.include_router(create_router(lambda: tmp_path, lambda persona: persona in {'alice', 'bob'}, write))
    with TestClient(app, headers={'x-write': 'allowed'}) as client:
        yield tmp_path, client


def _group(client, domain='media', persona='alice', **payload):
    response = client.post(f'/api/content/{domain}/groups', params={'persona': persona},
                           json={'name': '素材', **payload})
    assert response.status_code == 200, response.text
    return response.json()['id']


def _record(client, group, domain='media', persona='alice', **payload):
    response = client.post(f'/api/content/{domain}/records', params={'persona': persona},
                           json={'group_id': group, 'kind': 'image', 'title': '春天',
                                 'path': persona + '-project/photo.png', **payload})
    assert response.status_code == 200, response.text
    return response.json()


def test_media_groups_crud_ownership_pages_and_default_protection(library):
    _, client = library
    group = _group(client)
    record = _record(client, group, thumbnail_path='alice-project/photo.png')
    view = client.get('/api/content/media/groups', params={'persona': 'alice', 'title': '素材'}).json()
    assert view['items'][0]['thumbnails'] == ['alice-project/photo.png']
    assert view['total'] == 1
    assert client.get(f'/api/content/media/groups/{group}', params={'persona': 'bob'}).status_code == 404
    changed = client.put(f'/api/content/media/groups/{group}', params={'persona': 'alice'}, json={'name': '改名'})
    assert changed.json()['name'] == '改名'
    defaults = client.get('/api/content/media/groups', params={'persona': 'alice'}).json()['items']
    default = next(item for item in defaults if item['is_default'])
    assert client.delete(f'/api/content/media/groups/{default["id"]}', params={'persona': 'alice'}).status_code == 409
    assert client.delete(f'/api/content/media/groups/{group}', params={'persona': 'alice'}).status_code == 200
    moved = client.get(f'/api/content/media/records/{record["id"]}', params={'persona': 'alice'}).json()
    assert moved['group_id'] == default['id']
    assert client.get('/api/content/material/groups', params={'persona': 'alice'}).json()['total'] == 1
    assert client.get('/api/content/media/records', params={'persona': 'bob'}).json()['total'] == 0


def test_media_creation_requires_manifest_owner_and_bulk_delete_is_atomic(library):
    root, client = library
    alice, bob = _group(client), _group(client, persona='bob')
    a = _record(client, alice)
    b = _record(client, bob, persona='bob')
    forbidden = client.post('/api/content/media/records', params={'persona': 'alice'},
        json={'group_id': alice, 'kind': 'image', 'path': 'bob-project/photo.png'})
    assert forbidden.status_code == 403
    (root / 'alice-project' / '.easel.json').unlink()
    assert client.post('/api/content/media/records', params={'persona': 'alice'},
        json={'group_id': alice, 'kind': 'image', 'path': 'alice-project/photo.png'}).status_code == 403
    mixed = client.request('DELETE', '/api/content/media/records/bulk', params={'persona': 'alice'},
                            json={'ids': [a['id'], b['id']]})
    assert mixed.status_code == 404
    assert client.get(f'/api/content/media/records/{a["id"]}', params={'persona': 'alice'}).status_code == 200
    assert client.delete(f'/api/content/media/records/{a["id"]}', params={'persona': 'bob'}).status_code == 404


def test_transfer_copy_resets_usage_move_preserves_and_filters_delete(library):
    _, client = library
    first, second = _group(client), _group(client)
    record = _record(client, first)
    payload = {'ids': [record['id']], 'run_id': 'publication-1'}
    response = client.post('/api/content/media/usage', params={'persona': 'alice'}, json=payload)
    assert response.json()['items'][0]['use_count'] == 1
    transfer = {'ids': [record['id']], 'target_group_id': second, 'mode': 'copy'}
    copied = client.post('/api/content/media/transfer', params={'persona': 'alice'}, json=transfer).json()['items'][0]
    assert copied['id'] != record['id'] and copied['use_count'] == 0
    transfer['mode'] = 'move'
    moved = client.post('/api/content/media/transfer', params={'persona': 'alice'}, json=transfer).json()['items'][0]
    assert moved['use_count'] == 1 and moved['group_id'] == second
    page = client.get('/api/content/media/records', params={'persona': 'alice', 'page_size': 1}).json()
    assert page['total'] == 2 and len(page['items']) == 1
    assert client.delete('/api/content/media/records/filter', params={
        'persona': 'alice', 'group_id': second, 'min_use_count': 1}).json()['count'] == 1
    assert client.get(f'/api/content/media/records/{copied["id"]}', params={'persona': 'alice'}).status_code == 200


def test_material_optimal_platform_caps_and_concurrent_idempotent_usage(library):
    root, client = library
    group = _group(client, domain='material', platforms=['youtube', 'pinterest'])
    image = _record(client, group, domain='material', path='', account_types=['pinterest'])
    video = _record(client, group, domain='material', path='', kind='video', max_use_count=1,
                    media=[{'path': 'alice-project/video.mp4', 'kind': 'video'}])
    selected = client.get('/api/content/material/optimal', params={
        'persona': 'alice', 'group_id': group, 'platform': 'youtube'}).json()
    assert selected['id'] == video['id']
    scope = ContentScope(persona='alice', domain='material')
    def increment(_):
        return ContentLibrary(root).increment_usage(scope, UsageInput(ids=(video['id'],), run_id='same-run'))[0].use_count
    with ThreadPoolExecutor(max_workers=6) as workers:
        assert set(workers.map(increment, range(18))) == {1}
    assert client.get('/api/content/material/optimal', params={
        'persona': 'alice', 'group_id': group, 'platform': 'youtube'}).json() is None
    assert client.get('/api/content/material/optimal', params={
        'persona': 'alice', 'group_id': group, 'platform': 'pinterest'}).json()['id'] == image['id']
    assert client.get('/api/content/material/optimal', params={
        'persona': 'bob', 'group_id': group, 'platform': 'pinterest'}).status_code == 404
    updated = client.put(f'/api/content/material/records/{image["id"]}', params={'persona': 'alice'},
        json={'group_id': group, 'kind': 'article', 'title': '更新', 'topics': ['教程'], 'options': {'format': 'long'}})
    assert updated.json()['options'] == {'format': 'long'}


def test_write_guard_and_unknown_scope_are_enforced(library):
    _, client = library
    client.headers.pop('x-write')
    assert client.post('/api/content/media/groups', params={'persona': 'alice'}, json={'name': 'bad'}).status_code == 403
    assert client.get('/api/content/media/groups', params={'persona': 'unknown'}).status_code == 400


def test_single_material_delete_cleans_flagged_linked_records_and_preserves_source(library):
    root, client = library
    media_group = _group(client)
    media = _record(client, media_group)
    draft_group = _group(client, domain='material')
    draft = _record(client, draft_group, domain='material', path='', auto_delete_media=True,
        media=[{'path': 'alice-project/photo.png', 'media_id': media['id']}])
    assert client.delete(f'/api/content/material/records/{draft["id"]}', params={'persona': 'alice'}).status_code == 200
    assert client.get(f'/api/content/media/records/{media["id"]}', params={'persona': 'alice'}).status_code == 404
    assert (root / 'alice-project/photo.png').read_bytes() == b'image'
