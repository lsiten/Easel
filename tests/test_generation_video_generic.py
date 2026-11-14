"""User-visible generic video contracts over real HTTP, media and SQLite."""
import json

import pytest

from easel.content_library import ContentLibrary
from easel.content_library_models import ContentScope, GroupInput
from easel.local_user import LocalUsers
from easel.local_user_models import AIPreference
from tests.generic_video_support import generic as generic, body


@pytest.mark.parametrize('channel,path', [('openai', '/openai/videos'), ('volcengine', '/ark/contents/generations/tasks'),
    ('grok', '/v1/videos/generations'), ('dashscope', '/api/v1/services/aigc/video-generation/video-synthesis'),
    ('relay', '/api/ai/video/generations')])
def test_all_five_channels_archive_real_video_with_single_paid_submission(generic, channel, path):
    client, service, outputs, _, records, _ = generic
    created = client.post('/api/ai/video/generations', json=body(channel))
    assert created.status_code == 200 and created.json()['status'] == 'SUBMITTED', created.text
    assert records[0][1] == path
    headers = records[0][2]
    assert headers.get('x-api-key') == 'key-relay' if channel == 'relay' else headers['Authorization'] == 'Bearer key-' + channel
    if channel == 'dashscope':
        assert headers['X-DashScope-Async'] == 'enable'
    identifier = created.json()['id']
    result = client.get('/api/ai/video/generations/' + identifier, params={'persona': 'alice'})
    assert result.json()['status'] == 'SUCCESS' and result.json()['videoUrl'].startswith('/api/media/'), result.text
    job = service.store.get('alice', identifier)
    assert (outputs / job.asset.path).read_bytes()[4:8] == b'ftyp'
    assert sum(row[0] == 'POST' for row in records) == 1
    assert client.get('/api/ai/video/generations/' + identifier, params={'persona': 'bob'}).status_code == 404
    assert client.get('/api/ai/video/generations', params={'persona': 'alice'}).json()['total'] == 1
    assert client.get('/api/ai/video/generations', params={'persona': 'bob'}).json()['total'] == 0
    assert all('Authorization' not in row[2] and 'x-api-key' not in row[2] for row in records if row[1] == '/video.mp4')


def test_dashscope_mode_resolution_routing_is_frozen_and_replay_precedes_configuration(generic):
    client, service, outputs, settings, records, _ = generic
    LocalUsers(outputs).set_ai_kind('alice', 'video', AIPreference(default_model='horse', options={
        'channel': 'dashscope', 'modes': ['image2video'], 'resolutions': ['720P'], 'maxInputImages': 1,
        'runtimeModels': [{'model': 'fallback'}, {'model': 'specific', 'mode': 'image2video', 'resolution': '720P'}]}))
    payload = body('dashscope', model='horse', image='photos/image.png', resolution='720P', duration=3, seed=7, watermark=False)
    created = client.post('/api/ai/video/generations', json=payload)
    assert created.status_code == 200, created.text
    wire = json.loads(records[0][3])
    assert wire['model'] == 'specific' and wire['input']['media'][0]['type'] == 'first_frame'
    assert wire['input']['media'][0]['url'].startswith('data:image/png;base64,')
    assert wire['parameters'] == {'resolution': '720P', 'duration': 3, 'seed': 7, 'watermark': False}
    job = service.store.get('alice', created.json()['id'])
    assert job.runtime_model == 'specific' and job.effective_request.mode == 'image2video'
    del settings['DASHSCOPE_API_KEY']
    (outputs / 'photos/image.png').unlink()
    LocalUsers(outputs).set_ai_kind('alice', 'video', AIPreference(default_model='different'))
    replay = client.post('/api/ai/video/generations', json=payload)
    assert replay.json()['id'] == job.id and len(records) == 1
    paused = client.get('/api/ai/video/generations/' + job.id, params={'persona': 'alice'}).json()
    assert paused['status'] == 'InProgress' and len(records) == 1
    settings['DASHSCOPE_API_KEY'] = 'key-dashscope'
    assert client.get('/api/ai/video/generations/' + job.id, params={'persona': 'alice'}).json()['status'] == 'SUCCESS'
    assert sum(row[0] == 'POST' for row in records) == 1


def test_relay_retains_all_generic_options_and_owned_group_projection_is_idempotent(generic):
    client, service, outputs, _, records, _ = generic
    library = ContentLibrary(outputs)
    group = library.create_group(ContentScope(persona='alice', domain='media'), GroupInput(name='Generated', kind='video'))
    payload = body(groupId=group.id, image=['photos/image.png'], images=['photos/image.png'], image_tail='photos/image.png',
        video_url='photos/clip.mp4', videos=['photos/clip.mp4'], audios=[], mode='multi-image2video', size='custom', resolution='720p',
        ratio='16:9', duration=6, seed=19, watermark=True, tools=[{'type': 'web_search'}], metadata={'motion': 'slow'})
    created = client.post('/api/ai/video/generations', json=payload)
    assert created.status_code == 200, created.text
    wire = json.loads(records[0][3])
    assert 'groupId' not in wire and 'persona' not in wire and 'request_key' not in wire
    assert {key: wire[key] for key in ('mode', 'size', 'resolution', 'ratio', 'duration', 'seed', 'watermark', 'tools', 'metadata')} == {
        key: payload[key] for key in ('mode', 'size', 'resolution', 'ratio', 'duration', 'seed', 'watermark', 'tools', 'metadata')}
    assert wire['image'][0].startswith('data:image/png') and wire['image_tail'].startswith('data:image/png')
    assert wire['videos'][0].startswith('https://easel.example/api/ai/video/media/')
    identifier = created.json()['id']
    endpoint = '/api/ai/video/generations/' + identifier
    first = client.get(endpoint, params={'persona': 'alice'}).json()
    second = client.get(endpoint, params={'persona': 'alice'}).json()
    assert first['status'] == 'SUCCESS' and first['mediaId'] == second['mediaId'] and first['mediaId']
    with library.connect() as db:
        rows = db.execute('SELECT id FROM content_records WHERE group_id=?', (group.id,)).fetchall()
    assert len(rows) == 1 and rows[0]['id'] == first['mediaId']
    denied = client.post('/api/ai/video/generations', json=body(persona='bob', groupId=group.id))
    assert denied.status_code == 404 and sum(row[0] == 'POST' for row in records) == 1


@pytest.mark.parametrize('channel', ['grok', 'dashscope', 'relay'])
def test_unknown_submission_never_reposts_and_terminal_provider_failure_stays_failed(generic, channel):
    client, _, _, _, records, behavior = generic
    behavior['drop'] = True
    response = client.post('/api/ai/video/generations', json=body(channel)).json()
    assert response['status'] == 'UNKNOWN'
    replay = client.post('/api/ai/video/generations', json=body(channel)).json()
    assert response['id'] == replay['id'] and len(records) == 1
    behavior.update(drop=False, failed=True)
    created = client.post('/api/ai/video/generations', json=body(channel, request_key='two')).json()
    result = client.get('/api/ai/video/generations/' + created['id'], params={'persona': 'alice'}).json()
    assert result['status'] == 'FAILURE' and result['videoUrl'] is None
    assert len(records) == 3


@pytest.mark.parametrize('channel', ['grok', 'dashscope', 'relay'])
def test_generic_provider_identity_mismatch_prevents_foreign_artifact_download(generic, channel):
    client, _, _, _, records, behavior = generic
    created = client.post('/api/ai/video/generations', json=body(channel)).json()
    behavior['identity'] = 'foreign-task'
    result = client.get('/api/ai/video/generations/' + created['id'], params={'persona': 'alice'}).json()
    assert result['status'] == 'FAILURE' and result['error']['message'] == 'video_provider_identity_mismatch'
    assert len(records) == 2 and all(row[1] != '/video.mp4' for row in records)


def test_relay_nonzero_envelope_code_never_becomes_success(generic):
    client, _, _, _, records, behavior = generic
    behavior['relay_code'] = 42
    result = client.post('/api/ai/video/generations', json=body()).json()
    assert result['status'] == 'FAILURE' and result['error']['message'] == 'video_relay_rejected'
    assert client.post('/api/ai/video/generations', json=body()).json()['id'] == result['id']
    assert len(records) == 1


def test_source_catalog_pagination_status_and_date_wire_contracts(generic):
    from datetime import datetime
    client, _, _, _, _, _ = generic
    catalog = client.get('/api/ai/models/video/generation', params={'persona': 'alice'}).json()
    assert {item['channel'] for item in catalog} == {'openai', 'volcengine', 'grok', 'dashscope', 'relay'}
    assert all(item['configured'] and 'aspectRatios' in item and 'maxInputImages' in item for item in catalog)
    created = client.post('/api/ai/video/generations', json=body()).json()
    assert created['status'] == 'SUBMITTED' and datetime.fromisoformat(created['submittedAt']).tzinfo is not None
    page = client.get('/api/ai/video/generations', params={'persona': 'alice', 'pageSize': 1}).json()
    assert page['pageSize'] == 1 and page['totalPages'] == 1 and page['list'][0]['id'] == created['id']
    assert page['list'][0]['status'] == 'InProgress'
