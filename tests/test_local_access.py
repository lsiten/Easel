from fastapi.testclient import TestClient

from web import app as web


def test_key_once_hash_scope_permission_and_revocation(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    monkeypatch.setattr(web, 'profile_exists', lambda name: name in ('alice', 'bob'))
    local = TestClient(web.app, base_url='http://localhost:7860', headers={'Origin': 'http://localhost:7860'})
    created = local.post('/api/access-keys', json={'persona': 'alice', 'name': 'caller'}).json()
    token = created['key']
    listed = local.get('/api/access-keys?persona=alice').json()
    assert 'key' not in listed[0]
    assert token.encode() not in (tmp_path / '_access/access.sqlite3').read_bytes()
    remote = TestClient(web.app, base_url='http://localhost:7860', client=('192.168.1.5', 3000))
    headers = {'X-Easel-API-Key': token}
    assert remote.get('/api/short-links?persona=alice', headers=headers).status_code == 200
    assert remote.get('/api/short-links?persona=bob', headers=headers).status_code == 403
    assert remote.post('/api/short-links', headers=headers, json={'persona': 'alice', 'url': 'https://example.com'}).status_code == 403
    assert remote.get('/api/access-keys?persona=alice', headers=headers).status_code == 403
    assert local.delete(f"/api/access-keys/{created['id']}?persona=bob").status_code == 404
    assert local.delete(f"/api/access-keys/{created['id']}?persona=alice").status_code == 200
    assert remote.get('/api/short-links?persona=alice', headers=headers).status_code == 401


def test_delegated_write_and_durable_short_link(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    monkeypatch.setattr(web, 'profile_exists', lambda name: name == 'alice')
    local = TestClient(web.app, base_url='http://localhost:7860', headers={'Origin': 'http://localhost:7860'})
    key = local.post('/api/access-keys', json={'persona': 'alice', 'name': 'writer', 'permission': 'write'}).json()['key']
    remote = TestClient(web.app, base_url='http://localhost:7860', client=('192.168.1.5', 3000), headers={'Authorization': f'Bearer {key}'})
    result = remote.post('/api/short-links', json={'persona': 'alice', 'url': 'https://example.com/post?a=1'})
    assert result.status_code == 200, result.text
    path = result.json()['path']
    redirected = local.get(path, follow_redirects=False)
    assert redirected.status_code == 302 and redirected.headers['location'] == 'https://example.com/post?a=1'
    assert local.delete(f"/api/short-links/{result.json()['code']}?persona=alice").status_code == 200
    assert local.get(path).status_code == 404
    assert remote.post('/api/short-links?persona=bob', json={'persona': 'alice', 'url': 'https://example.com'}).status_code == 403
