"""Scoped caller credentials must never authorize application-wide configuration."""
import pytest
from fastapi.testclient import TestClient
from web import app as web
from tests.generation_support import generation as generation


@pytest.mark.parametrize('permission', ['read', 'write'])
@pytest.mark.parametrize('host', ['127.0.0.1', '192.168.1.50'])
def test_scoped_keys_cannot_admin_config_on_either_alias(generation, permission, host):
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as admin:
        created = admin.post('/api/access-keys', json={'persona': 'alice', 'name': 'config-boundary', 'permission': permission})
        assert created.status_code == 200, created.text
        current = admin.get('/api/config').json()
    with TestClient(web.app, base_url='http://localhost:7860', client=(host, 4001),
                    headers={'X-Easel-API-Key': created.json()['key']}) as delegated:
        statuses = []
        for prefix in ('/api/config', '/api/ai/config'):
            payload = {'config': current['config'], 'revision': current['revision']}
            statuses.extend((
                delegated.get(prefix + '?persona=alice').status_code,
                delegated.post(prefix + '/validate?persona=alice', json=payload).status_code,
                delegated.put(prefix + '?persona=alice', json=payload).status_code,
                delegated.post(prefix + '/restart?persona=alice').status_code,
            ))
        assert statuses == [403] * 8
        allowed = delegated.get('/api/ai/logs?persona=alice')
        assert allowed.status_code == 200, allowed.text
        assert delegated.get('/api/ai/logs?persona=bob').status_code == 403
