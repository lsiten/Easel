"""Native share grants use frozen owned bytes and actual provider completion."""
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from easel.channels import ChannelError
from easel.native_handoff import NativeHandoff
from easel.native_handoff_models import HandoffStart, HandoffTemplate
from easel.native_handoff_router import create_router
from easel.publish_models import RunState
from easel.rednote_share import _signature


@pytest.fixture
def harness(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / '.easel.json').write_text('{"profile":"alice"}')
    (project / 'image.jpg').write_bytes(b'approved-image')
    settings = {'EASEL_CHANNEL_MEDIA_BASE_URL': 'https://media.example',
        'EASEL_DOUYIN_CLIENT_ID': 'app', 'EASEL_DOUYIN_CLIENT_SECRET': 'secret',
        'EASEL_REDNOTE_CLIENT_ID': 'xhs-app', 'EASEL_REDNOTE_CLIENT_SECRET': 'xhs-secret',
        'EASEL_REDNOTE_ACCESS_TOKEN_URL': 'https://bridge.example/token'}
    calls = []
    completed = []
    def provider(request):
        calls.append(request)
        path = request.url.path
        if path == '/oauth/client_token/':
            value = {'access_token': 'private-app-token', 'expires_in': 7200}
        elif path == '/open/getticket/':
            value = {'ticket': 'private-ticket', 'expires_in': 7200}
        elif path == '/share-id/':
            value = {'share_id': 'server-share', **({'video_id': 'actual-video', 'open_id': 'actual-actor'} if completed else {})}
        elif request.url.host == 'bridge.example':
            body = json.loads(request.content)
            assert body['signature'] == _signature('xhs-app', body['nonce'], body['timestamp'], 'xhs-secret')
            return httpx.Response(200, json={'success': True, 'data': {'access_token': 'private-sdk-token'}})
        else:
            raise AssertionError(request.url)
        return httpx.Response(200, json={'data': value})
    service = NativeHandoff(tmp_path, lambda: settings, transport=httpx.MockTransport(provider))
    return service, calls, completed


def intent(**updates):
    return HandoffTemplate(persona='alice', request_key='template', title='Title', body='Body #话题',
        media=({'path': 'project/image.jpg', 'kind': 'image'},), reviewed=True, **updates)


def test_template_freezes_replays_revokes_and_hides_ownership(harness):
    service, calls, _ = harness
    template = service.create('douyin-open', intent())
    (service.root / 'project/image.jpg').unlink()
    assert service.create('douyin-open', intent()) == template
    assert service.media(template['template_id'], 0, template['token']).read_bytes() == b'approved-image'
    public = service.public_view(template['template_id'], template['token'])
    assert not {'persona', 'context', 'request_key', 'native_manifest'}.intersection(public)
    with pytest.raises(ChannelError, match='template_not_found'):
        service.public_view(template['template_id'], 'tampered')
    with pytest.raises(ChannelError, match='template_not_found'):
        service.owned('bob', template['template_id'])
    run = service.ledger.get_run(template['template_id'])
    lease = service.ledger.acquire_lease(run.run_id, 'test')
    service.ledger.transition(run.run_id, expected=run.state, target=RunState.CANCELLED, actor='test', fencing_token=lease.fencing_token)
    service.ledger.release_lease(run.run_id, lease.owner, lease.fencing_token)
    with pytest.raises(ChannelError, match='template_expired'):
        service.media(run.run_id, 0, template['token'])
    assert calls == []


def test_douyin_handoff_is_idempotent_and_waits_for_provider_proof(harness):
    service, calls, completed = harness
    template = service.create('douyin-open', intent())
    args = template['template_id'], template['token']
    request = HandoffStart(request_key='visitor', reviewed=True)
    action = service.start(*args, request)
    assert action['state'] == 'submitted' and action['publisher_identity'] == 'selected_in_app'
    query = parse_qs(urlsplit(action['user_action']['scheme']).query)
    assert query['title'] == ['Body'] and query['short_title'] == ['Title']
    assert json.loads(query['title_hashtag_list'][0]) == [{'name': '话题', 'start': 4}]
    assert 'private-app-token' not in json.dumps(action) and 'private-ticket' not in json.dumps(action)
    assert service.start(*args, request) == action
    assert len([call for call in calls if call.url.path == '/share-id/']) == 1
    assert service.status(*args, action['run_id'])['state'] == 'submitted'
    completed.append(True)
    published = service.status(*args, action['run_id'])
    assert published['state'] == 'published' and published['platform_work_id'] == 'actual-video'
    assert service.ledger.get_run(action['run_id']).payload['publisher_account_uid'] == 'actual-actor'
    assert len([call for call in calls if call.url.path == '/share-id/']) == 3
    service.status(*args, action['run_id'])
    assert len([call for call in calls if call.url.path == '/share-id/']) == 3


@pytest.mark.parametrize('change', ['images', 'duplicate', 'text', 'format', 'mixed', 'topics'])
def test_source_constraints_reject_before_grant(harness, change):
    service, calls, _ = harness
    payload = intent().model_dump()
    if change == 'text': payload['title'] = '😀' * 16
    if change == 'topics': payload['body'] = '#a #b #c #d #e #f'
    if change == 'duplicate': payload['media'] *= 2
    if change == 'images':
        payload['media'] = []
        for index in range(13):
            (service.root / f'project/{index}.jpg').write_bytes(b'image')
            payload['media'].append({'kind': 'image', 'path': f'project/{index}.jpg'})
    if change == 'format':
        (service.root / 'project/image.webp').write_bytes(b'image')
        payload['media'][0]['path'] = 'project/image.webp'
    if change == 'mixed':
        (service.root / 'project/video.mp4').write_bytes(b'video')
        payload['media'] += ({'kind': 'video', 'path': 'project/video.mp4'},)
    with pytest.raises(ChannelError): service.create('douyin-open', HandoffTemplate.model_validate(payload))
    assert calls == [] and not service.ledger.list_runs(persona='alice')


def test_rednote_bridge_signs_public_config_and_manual_state(harness):
    service, _, _ = harness
    template = service.create('rednote-link', intent())
    action = service.start(template['template_id'], template['token'], HandoffStart(request_key='visitor', reviewed=True))
    config = action['verifyConfig']
    assert config['signature'] == _signature('xhs-app', config['nonce'], config['timestamp'], 'private-sdk-token')
    assert action['state'] == 'ready_for_manual_submission' and action['sdk_method'] == 'xhs.share'
    assert 'private-sdk-token' not in json.dumps(action)
    assert all(run.state != RunState.PUBLISHED for run in service.ledger.list_runs(persona='alice'))


def test_router_revocation_public_media_and_explicit_review(harness):
    service, _, _ = harness
    app = FastAPI()
    app.include_router(create_router(lambda: service.root, service.settings, lambda p: p == 'alice', lambda r: None, transport=service.transport))
    client = TestClient(app)
    template = client.post('/api/native-handoff/douyin-open/templates', json=intent().model_dump(mode='json')).json()
    base = '/api/native-handoff/templates/' + template['template_id']
    params = {'token': template['token']}
    assert client.get(base + '/media/0', params=params).content == b'approved-image'
    assert client.post(base + '/start', params=params, json={'request_key': 'visitor', 'reviewed': False}).status_code == 403
    assert client.delete('/api/native-handoff/douyin-open/templates/' + template['template_id'], params={'persona': 'alice'}).status_code == 200
    assert client.get(base, params=params).status_code == 410
