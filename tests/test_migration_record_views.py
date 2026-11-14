"""Record read APIs preserve link annotations and publication-time summaries."""
from fastapi.testclient import TestClient
import pytest

from easel.publish_models import RunState
from web import app as web


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    monkeypatch.setattr(web, 'profile_exists', lambda name: name in {'alice', 'bob'})
    return TestClient(web.app, base_url='http://localhost:7860',
                      headers={'Origin': 'http://localhost:7860'})


def published_run(published_at: str):
    ledger = web._publish_ledger()
    run = ledger.create_run(platform='twitter', persona='alice', account_id='actor',
        idempotency_key='record-view', approval_digest='approved', media_manifest_hash='empty',
        payload={'title': 'Reviewed work', 'published_at': published_at})
    with ledger._connect() as database:
        database.execute('UPDATE publish_runs SET created_at=? WHERE run_id=?',
                         ('2025-01-01T00:00:00+00:00', run.run_id))
    for old, new in ((RunState.PREPARED, RunState.APPROVED), (RunState.APPROVED, RunState.QUEUED),
                     (RunState.QUEUED, RunState.RUNNING), (RunState.RUNNING, RunState.PUBLISHED)):
        ledger.transition(run.run_id, expected=old, target=new, actor='offline-test')
    return run.run_id


def test_link_annotation_survives_http_detail_and_history_reopen(client):
    run_id = published_run('2026-01-01T16:30:00+00:00')
    path = f'/api/publish-records/{run_id}/work-link'
    assert client.patch(path, json={'persona': 'bob', 'status': 'error'}).status_code == 404
    response = client.patch(path, json={'persona': 'alice', 'status': 'error'})
    assert response.status_code == 200, response.text
    assert response.json()['work_link_status'] == 'error'
    detail = client.get(f'/api/publish-records/{run_id}', params={'persona': 'alice'})
    assert detail.status_code == 200 and detail.json()['work_link_status'] == 'error'
    history = client.get('/api/publish-records', params={'persona': 'alice'})
    assert history.json()['items'][0]['work_link_status'] == 'error'


def test_summary_filters_actual_publish_time_and_groups_china_date(client):
    published_run('2026-01-01T16:30:00+00:00')
    response = client.get('/api/publish-records/summary', params={
        'persona': 'alice', 'start': '2026-01-01T16:00:00+00:00', 'end': '2026-01-01T17:00:00+00:00'})
    assert response.status_code == 200, response.text
    assert response.json() == {'total': 1, 'daily': [{'date': '2026-01-02', 'count': 1}],
                               'platforms': {'twitter': 1}}
    excluded = client.get('/api/publish-records/summary', params={
        'persona': 'alice', 'end': '2026-01-01T16:29:59+00:00'})
    assert excluded.json()['total'] == 0
    assert client.get('/api/publish-records/summary', params={'persona': 'bob'}).json()['total'] == 0


def test_native_task_update_submit_reaches_its_service_before_generic_controls(client):
    response = client.post('/api/publish-flows/tasks/missing-task/update', json={
        'persona': 'alice', 'account_uid': 'actor', 'title': 'Reviewed title', 'reviewed': True,
        'request_key': 'route-order-probe', 'viewed_revision': 'a' * 64,
        'approval_digest': 'b' * 64, 'approval_expires_at': 2_000_000_000})
    assert response.status_code == 404
    assert response.json()['detail'] == {'platform': 'update', 'code': 'task_not_found'}


def test_official_action_uses_server_receipt_without_exposing_private_fields(client):
    from easel._channel_publish_models import ChannelProgress
    from easel.channels.models import PublishReceipt
    receipt = PublishReceipt(state='requires_user_action', external_request_id='server-share', details={
        'schema': 'snssdk1128://openplatform/share?signature=approved', 'expires_at': 2_000_000_000,
        'private_upload_token': 'secret'})
    progress = ChannelProgress(stage='publication_user_action', receipt=receipt)
    run = web._publish_ledger().create_run(platform='douyin-open', persona='alice', account_id='actor',
        idempotency_key='action', approval_digest='a', media_manifest_hash='m',
        payload={'channel_progress': progress.model_dump(mode='json')}, initial_state=RunState.READY_FOR_MANUAL_SUBMISSION)
    web._publish_ledger().transition(run.run_id, expected=run.state, target=RunState.SUBMITTED, actor='offline-test')
    path = f'/api/publish-records/{run.run_id}/user-action'
    response = client.get(path, params={'persona': 'alice'})
    assert response.status_code == 200
    assert response.json()['scheme'] == receipt.details['schema']
    assert response.json()['expires_at'] == 2_000_000_000
    assert response.json()['requires_user_action'] is True
    assert 'secret' not in response.text and 'external_request_id' not in response.text
    assert client.get(path, params={'persona': 'bob'}).status_code == 404
