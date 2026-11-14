from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from easel.content_library import ContentLibrary
from easel.content_library_models import ContentScope, GroupInput, MediaReference, RecordInput
from easel.publish_ledger import PublishLedger
from easel.publish_projection import drain_publish_projections
from easel.publish_tasks import FlowItem, FlowRequest, TaskStore
from tests.test_channel_publishing import Harness, harness, intent


def source_records(harness: Harness, persona='alice'):
    library = ContentLibrary(harness.root)
    media_scope = ContentScope(persona=persona, domain='media')
    material_scope = ContentScope(persona=persona, domain='material')
    if persona == 'alice':
        path = 'project/image.jpg'
    else:
        project = harness.root / (persona + '-project')
        project.mkdir(exist_ok=True)
        (project / '.easel.json').write_text(json.dumps({'profile': persona}))
        (project / 'image.jpg').write_bytes(b'other-image')
        path = project.name + '/image.jpg'
    media_group = library.create_group(media_scope, GroupInput(name='媒体'))
    material_group = library.create_group(material_scope, GroupInput(name='草稿'))
    media = library.create(media_scope, RecordInput(group_id=media_group.id, kind='image', path=path))
    draft = library.create(material_scope, RecordInput(group_id=material_group.id, kind='article', title='Source draft',
        media=(MediaReference(path=path, media_id=media.id),)))
    context = {'materialId': draft.id, 'materialGroupId': material_group.id, 'mediaIds': [media.id],
               'taskId': 'external-task', 'source': 'content-library'}
    return library, media, draft, context


@pytest.mark.parametrize('foreign', ['material', 'media', 'group'])
def test_publish_precheck_rejects_foreign_source_context(harness: Harness, foreign):
    _, _, _, own = source_records(harness)
    _, _, _, other = source_records(harness, 'bob')
    key = {'material': 'materialId', 'media': 'mediaIds', 'group': 'materialGroupId'}[foreign]
    context = {**own, key: other[key]}
    response = harness.client.post('/api/channel-publish/twitter/precheck', json={**intent(), 'context': context})
    assert response.status_code in {403, 404}, response.text
    assert harness.provider.posts == []


def test_confirmed_publication_updates_usage_once_across_projection_ack_failure(harness: Harness, monkeypatch):
    library, media, draft, context = source_records(harness)
    payload = harness.approve('twitter', {**intent(), 'context': context})
    def crash(*_args, **_kwargs):
        raise sqlite3.OperationalError('crash after usage increment before effect acknowledgement')
    with monkeypatch.context() as patch:
        patch.setattr(PublishLedger, 'complete_effect', crash)
        response = harness.client.post('/api/channel-publish/twitter/publish', json=payload)
        assert response.status_code == 200 and response.json()['state'] == 'published'
    ledger = PublishLedger(harness.root / '_publish/ledger.sqlite3')
    assert len(ledger.pending_effects()) == 1
    assert library.get(ContentScope(persona='alice', domain='media'), media.id).use_count == 1
    assert library.get(ContentScope(persona='alice', domain='material'), draft.id).use_count == 1
    result = drain_publish_projections(ledger, harness.root)
    assert len(result.applied) == 1 and ledger.pending_effects() == ()
    assert harness.client.post('/api/channel-publish/twitter/publish', json=payload).json()['run_id'] == response.json()['run_id']
    assert library.get(ContentScope(persona='alice', domain='media'), media.id).use_count == 1
    assert library.get(ContentScope(persona='alice', domain='material'), draft.id).use_count == 1
    assert len(harness.provider.posts) == 1


def test_pending_and_failed_publications_do_not_consume_source_material(harness: Harness):
    library, media, draft, context = source_records(harness)
    payload = harness.approve('instagram', {**intent('project/image.jpg'), 'context': context})
    response = harness.client.post('/api/channel-publish/instagram/publish', json=payload)
    assert response.json()['state'] == 'submitted'
    assert library.get(ContentScope(persona='alice', domain='material'), draft.id).use_count == 0
    harness.provider.reject = True
    failed = harness.client.post(f'/api/channel-publish/instagram/{response.json()["run_id"]}/finalize',
                                 json={'persona': 'alice', 'account_uid': 'actor'})
    assert failed.json()['state'] == 'failed'
    assert library.get(ContentScope(persona='alice', domain='media'), media.id).use_count == 0
    assert library.get(ContentScope(persona='alice', domain='material'), draft.id).use_count == 0


def test_source_context_is_bound_to_reviewed_intent(harness: Harness):
    _, _, _, first = source_records(harness)
    _, _, _, second = source_records(harness)
    payload = harness.approve('twitter', {**intent(), 'context': first})
    response = harness.client.post('/api/channel-publish/twitter/publish', json={**payload, 'context': second})
    assert response.status_code == 409
    assert harness.provider.posts == []


def test_scheduled_flow_propagates_source_context_into_publication_intent(harness: Harness):
    _, _, _, context = source_records(harness)
    now = datetime.now(timezone.utc)
    store = TaskStore(harness.root / '_publish')
    flow = store.create(FlowRequest(persona='alice', flow_id='source-flow', publish_at=now - timedelta(seconds=1),
        content={'title': 'Reviewed'}, context=context, reviewed=True,
        items=(FlowItem(platform='twitter', account_uid='actor', approval_digest='a' * 64),)))
    claimed = store.claim('test-worker', now.timestamp())
    assert claimed[0] == flow['tasks'][0]['id']
    assert claimed[2]['context'] == context
