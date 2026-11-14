"""Provider time series survive the natural API and scoped snapshot storage."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
import pytest

from easel.analytics_snapshots import load_snapshots
from easel.channels import ChannelAdapter, Credential, CredentialStore
from easel.channels.router import create_router


def client(root, platform, provider, monkeypatch):
    original = ChannelAdapter.__init__
    def initialize(self, value, **kwargs):
        original(self, value, transport=httpx.MockTransport(provider), **kwargs)
    monkeypatch.setattr(ChannelAdapter, '__init__', initialize)
    CredentialStore(root / '_channels').save(Credential(persona='alice', platform=platform,
        account_uid='actor', access_token=SecretStr('offline-token')))
    app = FastAPI()
    app.include_router(create_router(lambda: root, lambda: {}, lambda name: name in {'alice', 'bob'}, lambda _: None))
    return TestClient(app)


def test_graph_history_keeps_every_point_and_forwards_the_date_range(tmp_path, monkeypatch):
    def provider(request):
        assert request.url.path.endswith('/actor/threads_insights')
        assert request.url.params['since'] == '2026-01-01' and request.url.params['until'] == '2026-01-03'
        return httpx.Response(200, json={'data': [
            {'name': 'views', 'values': [{'end_time': '2026-01-01T00:00:00Z', 'value': 10}, {'end_time': '2026-01-02T00:00:00Z', 'value': 20}]},
            {'name': 'likes', 'values': [{'end_time': '2026-01-01T00:00:00Z', 'value': 3}, {'end_time': '2026-01-02T00:00:00Z', 'value': 5}]},
        ]})
    api = client(tmp_path, 'threads', provider, monkeypatch)
    response = api.get('/api/channels/threads/analytics', params={'persona': 'alice', 'account_uid': 'actor',
        'start_date': '2026-01-01', 'end_date': '2026-01-03'})
    assert response.status_code == 200, response.text
    assert [point['account_metrics']['views'] for point in response.json()['snapshots']] == [10, 20]
    rows = [row for row in load_snapshots(tmp_path, 'alice', 'threads') if row.source == 'official_api_history']
    assert [dict(row.account_metrics)['likes'] for row in rows] == [3, 5]
    assert all(row.account_uid == 'actor' and row.observed_at.endswith('+00:00') and row.raw_payload_hash for row in rows)
    assert dict(rows[0].account_metrics)['followers'] is None
    assert load_snapshots(tmp_path, 'bob', 'threads') == ()


def test_pinterest_daily_work_metrics_keep_work_identity_and_all_days(tmp_path, monkeypatch):
    def provider(request):
        if request.url.path.endswith('/analytics'):
            assert request.url.params['start_date'] == '2026-01-01' and request.url.params['end_date'] == '2026-01-03'
            return httpx.Response(200, json={'all': {'daily_metrics': [
                {'date': '2026-01-01', 'metrics': {'IMPRESSION': 11, 'SAVE': 2}},
                {'date': '2026-01-02', 'metrics': {'IMPRESSION': 23, 'SAVE': 4}},
            ]}})
        assert request.url.path.endswith('/pins/pin-1')
        return httpx.Response(200, json={'id': 'pin-1', 'owner': {'id': 'actor'}})
    api = client(tmp_path, 'pinterest', provider, monkeypatch)
    response = api.get('/api/channels/pinterest/analytics', params={'persona': 'alice', 'account_uid': 'actor',
        'work_id': 'pin-1', 'start_date': '2026-01-01', 'end_date': '2026-01-03'})
    assert response.status_code == 200, response.text
    rows = [row for row in load_snapshots(tmp_path, 'alice', 'pinterest') if row.source == 'official_api_history']
    assert [dict(row.content_metrics)['impressions'] for row in rows] == [11, 23]
    assert [row.works[0].platform_work_id for row in rows] == ['pin-1', 'pin-1']


@pytest.mark.parametrize('start,end', [('2026-01-03', '2026-01-01'), ('invalid', '2026-01-03')])
def test_invalid_date_range_stops_before_provider_read(tmp_path, monkeypatch, start, end):
    def provider(request):
        raise AssertionError('Invalid dates reached the provider')
    api = client(tmp_path, 'threads', provider, monkeypatch)
    response = api.get('/api/channels/threads/analytics', params={'persona': 'alice', 'account_uid': 'actor',
        'start_date': start, 'end_date': end})
    assert response.status_code == 400 and response.json()['detail']['code'] == 'analytics_date_range_invalid'
