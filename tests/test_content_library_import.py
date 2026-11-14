import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from easel.content_library_router import create_router
from easel.ownership import OwnershipError, owned_media_path


@pytest.fixture
def remote_library(tmp_path, monkeypatch):
    import easel.remote_media as remote
    contacted = []
    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            contacted.append(self.path)
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/private')
                self.end_headers()
                return
            body = b'public-image'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            return
    original_dns, original_connect = socket.getaddrinfo, remote.connect_public
    def dns(host, port, *args, **kwargs):
        if host == 'media.example':
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port or 80))]
        return original_dns(host, port, *args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    with ThreadingHTTPServer(('127.0.0.1', 0), Source) as source:
        thread = threading.Thread(target=source.serve_forever, daemon=True)
        thread.start()
        def connect(host, port, timeout):
            if host == 'media.example':
                return socket.create_connection(('127.0.0.1', source.server_port), timeout)
            return original_connect(host, port, timeout)
        monkeypatch.setattr(remote, 'connect_public', connect)
        app = FastAPI()
        app.include_router(create_router(lambda: tmp_path, lambda name: name in {'alice', 'bob'}, lambda request: None))
        with TestClient(app) as client:
            yield tmp_path, client, contacted
        source.shutdown()
        thread.join(timeout=2)


def test_remote_public_import_is_owned_idempotent_and_redacts_source_query(remote_library):
    root, client, contacted = remote_library
    group = client.get('/api/content/media/groups', params={'persona': 'alice'}).json()['items'][0]['id']
    payload = {'request_id': 'import-1', 'group_id': group, 'kind': 'image',
               'url': 'http://media.example/photo.png?source_token=secret', 'title': 'Imported'}
    response = client.post('/api/content/media/import-url', params={'persona': 'alice'}, json=payload)
    assert response.status_code == 200, response.text
    record = response.json()
    assert client.post('/api/content/media/import-url', params={'persona': 'alice'}, json=payload).json()['id'] == record['id']
    assert len(contacted) == 1
    identity, path = owned_media_path(root, 'alice', record['path'])
    assert path.read_bytes() == b'public-image'
    metadata = json.loads(identity.manifest.read_text())
    assert metadata['source'] == 'http://media.example/photo.png'
    assert len(metadata['source_sha256']) == 64
    with pytest.raises(OwnershipError):
        owned_media_path(root, 'bob', record['path'])
    conflict = client.post('/api/content/media/import-url', params={'persona': 'alice'},
                           json={**payload, 'title': 'Changed'})
    assert conflict.status_code == 409
    assert len(contacted) == 1


def test_remote_import_blocks_redirected_private_victim_and_wrong_owner(remote_library):
    root, client, contacted = remote_library
    group = client.get('/api/content/media/groups', params={'persona': 'alice'}).json()['items'][0]['id']
    payload = {'request_id': 'redirect-1', 'group_id': group, 'kind': 'image', 'url': 'http://media.example/redirect'}
    assert client.post('/api/content/media/import-url', params={'persona': 'bob'}, json=payload).status_code == 404
    assert contacted == []
    rejected = client.post('/api/content/media/import-url', params={'persona': 'alice'}, json=payload)
    assert rejected.status_code == 400
    assert contacted == ['/redirect']
    assert client.get('/api/content/media/records', params={'persona': 'alice'}).json()['total'] == 0
    assert not list(root.glob('import-*/media.*'))
