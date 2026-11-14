"""DirectEdit HTTP source contracts, durable replay and real owned video outputs."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import socket
import subprocess
import sys
import threading
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from easel.generation_direct_edit_router import create_router
from easel.generation_direct_edit_service import DirectEditGeneration
from easel.generation_store import GenerationStore
from easel.ownership import owned_media_path, OwnershipError


@pytest.fixture
def direct_edit(tmp_path, monkeypatch):
    clip = tmp_path / 'clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:d=0.2',
                    '-c:v', 'mpeg4', str(clip)], check=True, capture_output=True)
    records = []
    behavior = {'drop': False, 'invalid': False, 'result': [], 'entered': None, 'release': None}
    class Provider(BaseHTTPRequestHandler):
        def respond(self, value):
            content = value if isinstance(value, bytes) else json.dumps({'Result': value}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        def do_POST(self):
            query = parse_qs(urlsplit(self.path).query)
            action = query['Action'][0]
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            records.append((action, query['Version'][0], payload))
            if action == 'SubmitDirectEditTaskAsync':
                if behavior['drop']:
                    self.close_connection = True
                    return
                self.respond({'ReqId': 'req-123'})
            elif action == 'GetDirectEditResult':
                if behavior['entered'] is not None:
                    behavior['entered'].set()
                    assert behavior['release'].wait(10), 'concurrent poll never released'
                self.respond(behavior['result'])
            else:
                self.send_error(400)
        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            if query.get('Action') == ['GetMediaInfos']:
                records.append(('GetMediaInfos', query['Version'][0], query))
                self.respond({'MediaInfoList': [{'SourceInfo': {'FileName': 'clip.mp4', 'Width': 16, 'Height': 16}}]})
            else:
                self.respond(b'broken-video' if behavior['invalid'] else clip.read_bytes())
        def log_message(self, *args):
            return
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    settings = {'VOLCENGINE_ACCESS_KEY_ID': 'fixture-ak', 'VOLCENGINE_SECRET_ACCESS_KEY': 'fixture-sk',
                'VOLCENGINE_SPACE_NAME': 'default-space', 'VOLCENGINE_VOD_BASE_URL': base,
                'VOLCENGINE_PLAYBACK_BASE_URL': base, 'VOLCENGINE_URL_AUTH_PRIMARY_KEY': 'play-secret'}
    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    import easel.remote_media as download
    monkeypatch.setattr(download, 'validate_public_url', lambda url: url)
    monkeypatch.setattr(download, 'connect_public', lambda host, port, timeout: socket.create_connection(('127.0.0.1', server.server_port), timeout))
    factory = lambda: DirectEditGeneration(outputs, settings)
    app = FastAPI()
    def access(request):
        if request.headers.get('x-access') != 'allowed':
            raise HTTPException(403, 'access_required')
    app.include_router(create_router(factory, lambda persona: persona in ('alice', 'bob'), access))
    with TestClient(app, headers={'x-access': 'allowed'}) as client:
        yield client, outputs, records, behavior, factory, clip
    server.shutdown()
    server.server_close()
    thread.join()


def submit(client, **changes):
    body = {'persona': 'alice', 'request_key': 'one',
            'Track': [[{'Type': 'video', 'Source': 'vid://input', 'TargetTime': [0, 200]}]]}
    body.update(changes)
    return client.post('/api/ai/aideo/video-edit/tasks', json=body)


def detail(client, identifier, persona='alice'):
    return client.get(f'/api/ai/aideo/video-edit/tasks/{identifier}', params={'persona': persona})


def test_native_payload_infers_canvas_maps_codec_and_preserves_edit_fields(direct_edit):
    client, outputs, records, behavior, factory, clip = direct_edit
    result = submit(client, Output={'Codec': {'VideoBitRate': 1234, 'Crf': 20, 'Preset': 'fast'}, 'Alpha': False},
                    Priority=0, CallbackArgs='opaque', CallbackUrl='https://example.com/callback')
    assert result.json()['state'] == 'running'
    action, version, payload = next(row for row in records if row[0] == 'SubmitDirectEditTaskAsync')
    assert version == '2018-01-01'
    assert (payload['Uploader'], payload['Application'], payload['Priority'], payload['CallbackArgs']) == ('default-space', 'VideoTrackToB', 0, 'opaque')
    assert payload['EditParam']['Canvas'] == {'Width': 16, 'Height': 16}
    assert payload['EditParam']['Output'] == {'Alpha': False, 'Codec': {'VideoBitrate': 1234, 'Crf': 20, 'Preset': 'fast'}}
    assert payload['EditParam']['Upload']['SpaceName'] == 'default-space'
    assert factory().store.get('alice', result.json()['taskId']).provider_id == 'req-123'


def test_native_parameters_and_extended_filters_are_lossless(direct_edit):
    client, outputs, records, behavior, factory, clip = direct_edit
    edit = {'Canvas': {'Width': 32, 'Height': 64, 'BackgroundColor': '#ffffffff'},
            'Track': [[{'Type': 'text_roll', 'TargetTime': [0, 200], 'Text': 'hello', 'FontID': 'font',
                       'Extra': [{'Type': 'transform', 'PosX': 0, 'PosY': 1, 'Width': 32, 'Height': 64},
                                 {'Type': 'a_fade', 'FadeIn': 50}, {'Type': 'delogo', 'Sigma': 12}]}]],
            'Upload': {'SpaceName': 'upload-space', 'FileName': 'custom.mp4'}, 'Uploader': 'legacy'}
    request = {'persona': 'alice', 'request_key': 'native', 'EditParam': edit,
               'Application': 'VideoTrackHighlight', 'SpaceName': 'space', 'Uploader': 'uploader'}
    response = client.post('/api/ai/aideo/video-edit/tasks', json=request)
    assert response.json()['state'] == 'running'
    assert records == [('SubmitDirectEditTaskAsync', '2018-01-01',
                        {'Uploader': 'uploader', 'Application': 'VideoTrackHighlight', 'EditParam': edit})]


def test_replay_scope_conflict_access_and_ambiguous_submission_survive_restart(direct_edit):
    client, outputs, records, behavior, factory, clip = direct_edit
    behavior['drop'] = True
    identifier = submit(client).json()['taskId']
    behavior['drop'] = False
    assert submit(client).json()['taskId'] == identifier
    assert submit(client, Priority=2).status_code == 409
    assert factory().store.get('alice', identifier).state == 'unknown'
    assert factory().store.candidates() == ()
    assert detail(client, identifier).json()['state'] == 'unknown'
    assert detail(client, identifier, 'bob').status_code == 404
    assert detail(client, identifier, 'invalid').status_code == 400
    assert client.get(f'/api/ai/aideo/video-edit/tasks/{identifier}?persona=alice', headers={'x-access': 'denied'}).status_code == 403
    assert len([row for row in records if row[0] == 'SubmitDirectEditTaskAsync']) == 1


@pytest.mark.parametrize('result,expected', [([], 'running'), ({'Status': 'pending'}, 'running'),
    ([{'ReqId': 'req-123', 'Status': 'start'}], 'running'), ({'Status': 'Processing'}, 'running'),
    ({'Status': 'failed_run', 'Message': 'fixture-sk failed'}, 'failed'),
    ({'Status': 'user_canceled'}, 'failed'), ({'Status': 'mystery'}, 'running'),
    (None, 'running'), ({'ReqId': 'foreign', 'Status': 'success', 'OutputVid': 'out'}, 'running')])
def test_result_envelope_and_status_semantics(direct_edit, result, expected):
    client, outputs, records, behavior, factory, clip = direct_edit
    identifier = submit(client).json()['taskId']
    behavior['result'] = result
    response = detail(client, identifier)
    assert response.json()['state'] == expected
    assert 'fixture-sk' not in response.text
    assert GenerationStore(outputs).assets('alice').items == ()
    assert records[-1] == ('GetDirectEditResult', '2018-01-01', {'ReqIds': ['req-123']})


def test_restart_reconcile_archives_ffprobe_valid_owned_video_and_native_receipt(direct_edit):
    client, outputs, records, behavior, factory, clip = direct_edit
    identifier = submit(client).json()['taskId']
    assert factory().store.candidates() == (('alice', identifier),)
    restarted = subprocess.run([sys.executable, '-c',
        'import sys; from pathlib import Path; from easel.generation_direct_edit_service import DirectEditGeneration; '
        'job=DirectEditGeneration(Path(sys.argv[1]), {}).store.get("alice",sys.argv[2]); print(job.provider_id)',
        str(outputs), identifier], check=True, capture_output=True, text=True)
    assert restarted.stdout.strip() == 'req-123'
    behavior.update(invalid=True, result=[{'ReqId': 'req-123', 'Status': 'success', 'OutputVid': 'out'}])
    assert detail(client, identifier).json()['state'] == 'running'
    assert factory().store.get('alice', identifier).response.OutputVid == 'out'
    assert GenerationStore(outputs).assets('alice').items == ()
    behavior['invalid'] = False
    response = detail(client, identifier).json()
    assert response['state'] == 'succeeded'
    assets = GenerationStore(outputs).assets('alice').items
    assert len(assets) == 1
    asset = assets[0]
    saved = owned_media_path(outputs, 'alice', asset.path)[1]
    assert (saved.read_bytes(), asset.sha256, asset.size) == (clip.read_bytes(), hashlib.sha256(clip.read_bytes()).hexdigest(), clip.stat().st_size)
    assert response['outputUrl'] == '/api/media/' + quote(asset.path, safe='/')
    assert factory().store.get('alice', identifier).response.OutputUrl == response['outputUrl']
    assert GenerationStore(outputs).assets('bob').items == ()
    with pytest.raises(OwnershipError):
        owned_media_path(outputs, 'bob', asset.path)
    assert factory().store.candidates() == ()
    before = len(records)
    assert detail(client, identifier).json()['state'] == 'succeeded'
    assert len(records) == before
    assert len([row for row in records if row[0] == 'SubmitDirectEditTaskAsync']) == 1


def test_missing_canvas_and_invalid_request_never_submit_paid_call(direct_edit):
    client, outputs, records, behavior, factory, clip = direct_edit
    assert submit(client, Track=[[{'Type': 'text', 'Text': 'hello'}]]).json()['state'] == 'failed'
    assert submit(client, request_key='invalid', Priority=10).status_code == 422
    assert submit(client, request_key='empty', Track=[]).status_code == 422
    assert records == []


def test_simultaneous_poll_is_serialized_and_dead_claim_is_recovered(direct_edit):
    client, outputs, records, behavior, factory, clip = direct_edit
    identifier = submit(client).json()['taskId']
    behavior.update(entered=threading.Event(), release=threading.Event(),
                    result={'Status': 'success', 'OutputVid': 'out'})
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(detail, client, identifier)
        assert behavior['entered'].wait(10)
        try:
            assert detail(client, identifier).json()['state'] == 'running'
            assert len([row for row in records if row[0] == 'GetDirectEditResult']) == 1
        finally:
            behavior['release'].set()
        assert first.result(timeout=10).json()['state'] == 'succeeded'
    assert len(GenerationStore(outputs).assets('alice').items) == 1
    second = submit(client, request_key='two').json()['taskId']
    dead = subprocess.run([sys.executable, '-c', 'import os; print(os.getpid())'], check=True, capture_output=True, text=True)
    with factory().store.base.connect() as db:
        db.execute('INSERT OR REPLACE INTO direct_edit_poll_claims VALUES(?,?,?)', (second, dead.stdout.strip() + ':dead', 0))
    assert detail(client, second).json()['state'] == 'succeeded'


def test_direct_output_url_is_localized_and_paid_replay_works_without_credentials(direct_edit):
    import anyio
    client, outputs, records, behavior, factory, clip = direct_edit
    identifier = submit(client).json()['taskId']
    request = factory().store.get('alice', identifier).request
    assert anyio.run(DirectEditGeneration(outputs, {}).submit, request).id == identifier
    behavior['result'] = {'Status': 'Completed', 'OutputUrl': 'http://fixture.example/clip.mp4?token=fixture-sk',
                          'Message': 'fixture-ak fixture-sk play-secret'}
    response = detail(client, identifier)
    assert response.json()['state'] == 'succeeded'
    assert response.json()['outputUrl'].startswith('/api/media/')
    record = factory().store.get('alice', identifier).model_dump_json()
    for secret in ('fixture-ak', 'fixture-sk', 'play-secret', 'fixture.example'):
        assert secret not in record
