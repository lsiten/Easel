"""Aideo HTTP contracts exercised through real local VOD, SQLite and FFprobe."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import subprocess
import threading
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from easel.generation_aideo_router import create_router
from easel.generation_aideo_service import AideoGeneration
from easel.generation_store import GenerationStore


@pytest.fixture
def aideo(tmp_path, monkeypatch):
    clip = tmp_path / 'clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:d=0.2',
                    '-c:v', 'mpeg4', str(clip)], check=True, capture_output=True)
    records = []
    behavior = {'drop': False, 'invalid': False, 'response': {'Status': 'Processing'}}
    class Provider(BaseHTTPRequestHandler):
        def respond(self, value):
            content = value if isinstance(value, bytes) else json.dumps({'Result': value}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        def do_POST(self):
            action = parse_qs(urlsplit(self.path).query)['Action'][0]
            records.append(('Submit' if action == 'SubmitAideoTaskAsync' else action, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            if behavior['drop']:
                self.close_connection = True
                return
            self.respond({'VCreativeId': 'style-123'} if action == 'AsyncVCreativeTask' else
                         {'TaskId': 'recap-123', 'DramaScriptTaskId': 'script-123'} if action == 'CreateDramaRecapTask' else {'TaskId': 'native-123'})
        def do_PUT(self):
            content = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            query = parse_qs(urlsplit(self.path).query)
            if self.path.endswith('?uploads'):
                self.respond(b'{"payload":{"uploadID":"multipart-id"}}')
            elif 'partNumber' in query:
                import zlib
                if self.headers['Content-CRC32'] != f'{zlib.crc32(content):08x}':
                    self.send_error(400)
                    return
                records.append(('Part', (query['partNumber'][0], len(content))))
                self.respond(b'')
            elif 'uploadID' in query:
                records.append(('Merge', content.decode()))
                self.respond(b'')
            else:
                records.append(('Upload', len(content)))
                self.respond(b'')
        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            action = query.get('Action', ['media'])[0]
            records.append((action, query))
            if action in ('GetAideoTaskResult', 'GetVCreativeTaskResult', 'QueryDramaRecapTask'):
                self.respond(behavior['response'])
            elif action == 'ApplyUploadInfo':
                self.respond({'Data': {'UploadAddress': {'SessionKey': 'upload-session', 'UploadHosts': ['upload.example'],
                    'StoreInfos': [{'StoreUri': 'media/source', 'Auth': 'upload-auth'}]}}})
            elif action == 'CommitUploadInfo':
                self.respond({'Data': {'Vid': 'uploaded-vid'}})
            elif action == 'GetMediaInfos':
                self.respond({'MediaInfoList': [{'SourceInfo': {'FileName': 'clip.mp4'}}]})
            else:
                self.respond(b'invalid-video' if behavior['invalid'] else clip.read_bytes())
        def log_message(self, *args):
            return
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    settings = {'VOLCENGINE_ACCESS_KEY_ID': 'fixture-ak', 'VOLCENGINE_SECRET_ACCESS_KEY': 'fixture-sk',
                'VOLCENGINE_SPACE_NAME': 'space', 'VOLCENGINE_VOD_BASE_URL': base,
                'VOLCENGINE_PLAYBACK_BASE_URL': base, 'VOLCENGINE_URL_AUTH_PRIMARY_KEY': 'play-secret'}
    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    project = outputs / 'project'
    project.mkdir()
    (project / '.easel.json').write_text(json.dumps({'profile': 'alice'}))
    (project / 'clip.mp4').write_bytes(clip.read_bytes())
    # Only socket routing and public-address policy are substituted for the local fixture.
    import easel.remote_media as download
    monkeypatch.setattr(download, 'validate_public_url', lambda url: url)
    monkeypatch.setattr(download, 'connect_public', lambda host, port, timeout: socket.create_connection(('127.0.0.1', server.server_port), timeout))
    original_send = httpx.AsyncClient.send
    async def local_upload(client, request, **kwargs):
        if request.url.host == 'upload.example':
            request.url = httpx.URL(base + request.url.path)
        return await original_send(client, request, **kwargs)
    monkeypatch.setattr(httpx.AsyncClient, 'send', local_upload)
    factory = lambda: AideoGeneration(outputs, settings)
    app = FastAPI()
    def access(request):
        if request.headers.get('x-access') != 'allowed':
            raise HTTPException(403, 'access_required')
    app.include_router(create_router(factory, lambda persona: persona in ('alice', 'bob'), access))
    with TestClient(app, headers={'x-access': 'allowed'}) as client:
        yield client, outputs, records, behavior, factory, base
    server.shutdown()
    server.server_close()
    thread.join()


def submit(client, **changes):
    body = {'persona': 'alice', 'request_key': 'one', 'prompt': 'edit', 'multiInputs': ['vid://input']}
    body.update(changes)
    return client.post('/api/ai/aideo/tasks', json=body)


def test_prompt_submission_replays_and_paginates_without_cross_persona_access(aideo):
    client, outputs, records, behavior, factory, base = aideo
    first = submit(client).json()['taskId']
    assert submit(client).json()['taskId'] == first
    assert submit(client, prompt='different').status_code == 409
    second = submit(client, request_key='two').json()['taskId']
    page = client.get('/api/ai/aideo/tasks?persona=alice&pageSize=1').json()
    assert (page['total'], [row['taskId'] for row in page['list']]) == (2, [second])
    assert client.get(f'/api/ai/aideo/tasks/{first}?persona=bob').status_code == 404
    assert client.get('/api/ai/aideo/tasks?persona=bob').json()['total'] == 0
    assert [record[1]['MultiInputs'] for record in records if record[0] == 'Submit'] == [[{'Type': 'Vid', 'Vid': 'input'}]] * 2


def test_ambiguous_paid_submission_is_never_repeated(aideo):
    client, outputs, records, behavior, factory, base = aideo
    behavior['drop'] = True
    identifier = submit(client).json()['taskId']
    behavior['drop'] = False
    assert submit(client).json()['taskId'] == identifier
    detail = client.get(f'/api/ai/aideo/tasks/{identifier}?persona=alice').json()
    assert detail['state'] == 'unknown'
    assert len([row for row in records if row[0] == 'Submit']) == 1
    assert factory().store.get('alice', identifier).provider_id == ''


@pytest.mark.parametrize('skill,api', [
    ('AITranslation', {'AITranslation': {'ProjectInfo': {'Status': 'Exported', 'OutputVideo': {'Vid': 'output'}}}}),
    ('Erase', {'Erase': {'Output': {'Task': {'Erase': {'File': {'FileName': 'clip.mp4'}}}}}}),
    ('Highlight', {'Highlight': {'Edits': [{'Vid': 'output'}]}}),
    ('VCreative', {'VCreative': {'Status': 'success', 'OutputJson': '{"Result":{"Vid":"output"}}'}}),
    ('Vision', {'Vision': {'Description': 'a blue video'}}),
])
def test_skills_reconcile_after_restart_and_archive_real_owned_media(aideo, skill, api):
    client, outputs, records, behavior, factory, base = aideo
    request = {'persona': 'alice', 'request_key': 'skill', 'skillType': skill, 'skillParams': {'Language': 'en'}, 'multiInputs': ['vid://input']}
    identifier = client.post('/api/ai/aideo/tasks', json=request).json()['taskId']
    assert factory().store.get('alice', identifier).provider_id == 'native-123'
    behavior['response'] = {'Status': 'Completed', 'SkillType': skill, 'ApiResponses': [{'VodTaskType': skill, **api}]}
    detail = client.get(f'/api/ai/aideo/tasks/{identifier}?persona=alice').json()
    assert detail['status'] == 'Completed'
    assets = GenerationStore(outputs).assets('alice').items
    assert len(assets) == (0 if skill == 'Vision' else 1)
    if assets:
        from easel.ownership import owned_media_path
        assert owned_media_path(outputs, 'alice', assets[0].path)[1].stat().st_size == assets[0].size
    assert json.loads(next(row[1]['SkillParams'] for row in records if row[0] == 'Submit')) == {'Language': 'en'}


@pytest.mark.parametrize('response,state,error', [
    ({'Status': 'Completed', 'ApiResponses': [{'VodTaskType': 'AITranslation', 'AITranslation': {'ProjectInfo': {'Status': 'InExporting', 'OutputVideo': {'Vid': 'output'}}}}]}, 'running', None),
    ({'Status': 'Completed', 'ApiResponses': [{'VodTaskType': 'VCreative', 'VCreative': {'Status': 'failed_run', 'OutputJson': 'fixture-sk'}}]}, 'failed', 'aideo_vcreative_failed'),
    ({'Status': 'Completed', 'ApiResponses': []}, 'failed', 'aideo_result_empty'),
    ({'Status': 'Failed'}, 'failed', 'aideo_provider_task_failed'),
])
def test_source_result_readiness_and_failure_semantics(aideo, response, state, error):
    client, outputs, records, behavior, factory, base = aideo
    identifier = submit(client).json()['taskId']
    behavior['response'] = response
    detail = client.get(f'/api/ai/aideo/tasks/{identifier}?persona=alice')
    assert (detail.json()['state'], detail.json()['errorMessage']) == (state, error)
    assert 'fixture-sk' not in detail.text
    assert GenerationStore(outputs).assets('alice').items == ()


@pytest.mark.parametrize('kind', ['stream', 'url', 'string'])
def test_owned_stream_native_upload_contract_and_foreign_rejection(aideo, kind):
    client, outputs, records, behavior, factory, base = aideo
    size = (outputs / 'project/clip.mp4').stat().st_size
    media = [{'type': 'stream', 'stream': 'project/clip.mp4', 'fileSize': size}]
    inputs = media if kind == 'stream' else [{'type': 'url', 'url': base + '/clip.mp4'}] if kind == 'url' else [base + '/clip.mp4']
    identifier = submit(client, multiInputs=inputs).json()['taskId']
    assert factory().store.get('alice', identifier).provider_id == 'native-123'
    assert next(row for row in records if row[0] == 'Upload')[1] == size
    assert next(row for row in records if row[0] == 'Submit')[1]['MultiInputs'] == [{'Type': 'Vid', 'Vid': 'uploaded-vid'}]
    foreign = submit(client, persona='bob', multiInputs=media).json()['taskId']
    assert factory().store.get('bob', foreign).state == 'failed'
    assert len([row for row in records if row[0] == 'Upload']) == 1


def test_invalid_result_media_never_enters_assets_and_can_reconcile(aideo):
    client, outputs, records, behavior, factory, base = aideo
    identifier = submit(client).json()['taskId']
    behavior.update(invalid=True, response={'Status': 'Completed', 'ApiResponses': [{'VodTaskType': 'Highlight', 'Highlight': {'Edits': [{'Vid': 'out'}]}}]})
    detail = client.get(f'/api/ai/aideo/tasks/{identifier}?persona=alice').json()
    assert detail['state'] == 'running'
    assert GenerationStore(outputs).assets('alice').items == ()
    behavior['invalid'] = False
    assert client.get(f'/api/ai/aideo/tasks/{identifier}?persona=alice').json()['state'] == 'succeeded'
    assert len([row for row in records if row[0] == 'Submit']) == 1


def test_request_variants_and_access_are_enforced_before_provider(aideo):
    client, outputs, records, behavior, factory, base = aideo
    assert submit(client, skillType='Erase').status_code == 422
    assert submit(client, multiInputs=[]).status_code == 422
    assert client.get('/api/ai/aideo/tasks?persona=alice&page=0').status_code == 422
    assert client.post('/api/ai/aideo/tasks', headers={'x-access': 'denied'}, json={'persona': 'alice', 'request_key': 'one', 'prompt': 'edit', 'multiInputs': ['vid://input']}).status_code == 403
    assert records == []


def test_vod_signing_matches_independent_node_crypto_vector():
    from easel.generation_aideo_provider import VodCall, VodConfig, signed_request
    request = signed_request(VodConfig('access', 'secret', 'test'), VodCall('SubmitAideoTaskAsync',
        {'SpaceName': 'test', 'MultiInputs': [{'Type': 'Vid', 'Vid': 'v1'}], 'Prompt': 'trim'},
        method='POST', version='2025-03-03'), '20260101T000000Z')
    assert request.headers['authorization'] == ('HMAC-SHA256 Credential=access/20260101/cn-north-1/vod/request, '
        'SignedHeaders=host;x-content-sha256;x-date, Signature=bfefb85e9364ae35dd66ee27c299d8176fb728b404125cb0214acf7dce80f3e1')
    assert request.content == b'{"SpaceName":"test","MultiInputs":[{"Type":"Vid","Vid":"v1"}],"Prompt":"trim"}'



def test_large_native_upload_splits_and_merges_crc_checked_parts(aideo, tmp_path):
    import anyio
    import zlib
    from easel.generation_aideo_provider import upload_bytes
    client, outputs, records, behavior, factory, base = aideo
    first = b'a' * (20 * 1024**2)
    last = b'last chunk'
    path = tmp_path / 'large.mp4'
    path.write_bytes(first + last)
    anyio.run(upload_bytes, path, base + '/storage', 'upload-auth')
    assert [row for row in records if row[0] == 'Part'] == [('Part', ('1', len(first))), ('Part', ('2', len(last)))]
    assert next(row[1] for row in records if row[0] == 'Merge') == f'1:{zlib.crc32(first):08x},2:{zlib.crc32(last):08x}'
