"""Source business service workflows, through HTTP and durable native receipts."""
from urllib.parse import unquote
import pytest
from tests.test_generation_aideo import aideo
from easel.generation_store import GenerationStore


def submit(client, workflow, **options):
    body = {'persona': 'alice', 'request_key': 'workflow'}
    body.update({'videoInput': 'vid://source', 'resolution': '720p'} if workflow == 'style-transfer' else {'vids': ['vid://source']})
    body.update(options)
    return client.post(f'/api/ai/aideo/{workflow}/tasks', json=body)


def test_style_transfer_native_scene_and_restart_owned_output(aideo):
    client, outputs, records, behavior, factory, base = aideo
    result = submit(client, 'style-transfer', style='漫画风').json()
    identifier = result['taskId']
    assert factory().store.get('alice', identifier).provider_id == 'style-123'
    assert submit(client, 'style-transfer', style='漫画风').json()['taskId'] == identifier
    assert next(row[1] for row in records if row[0] == 'AsyncVCreativeTask') == {
        'Scene': 'videostyletrans', 'Uploader': 'space',
        'ParamObj': {'input': 'vid://source', 'space_name': 'space', 'resolution': '720p', 'style': '漫画风'}}
    behavior['response'] = {'Status': 'success', 'OutputJson': '{"vid":"style-output"}'}
    detail = client.get(f'/api/ai/aideo/style-transfer/tasks/{identifier}?persona=alice').json()
    assert (detail['status'], detail['outputVid']) == ('Completed', 'style-output')
    asset, = GenerationStore(outputs).assets('alice').items
    assert unquote(detail['outputUrl']) == '/api/media/' + asset.path
    assert (outputs / asset.path).is_file()
    assert client.get(f'/api/ai/aideo/style-transfer/tasks/{identifier}?persona=bob').status_code == 404
    assert client.get(f'/api/ai/aideo/drama-recap/tasks/{identifier}?persona=alice').status_code == 404
    assert len([row for row in records if row[0] == 'AsyncVCreativeTask']) == 1


@pytest.mark.parametrize('custom', [True, False])
def test_drama_recap_configuration_and_script_receipt_survive_restart(aideo, custom):
    client, outputs, records, behavior, factory, base = aideo
    options = {'dramaScriptTaskId': 'existing-script', 'recapText': 'custom narration'} if custom else {}
    result = submit(client, 'drama-recap', **options, speakerConfig={'appId': 'app', 'cluster': 'voice', 'voiceType': 'narrator'},
        fontConfig={'color': '#fff', 'size': 24, 'name': 'font'}, recapStyle='suspense', recapTextLength=300).json()
    identifier = result['taskId']
    job = factory().store.get('alice', identifier)
    assert (job.provider_id, job.script_id, result['dramaScriptTaskId']) == ('recap-123', 'script-123', 'script-123')
    payload = next(row[1] for row in records if row[0] == 'CreateDramaRecapTask')
    assert payload['DramaRecapConfig'] == {'RecapTextSpeed': 1.2, 'PauseTime': 120, 'AllowRepeatMatch': False,
        'RecapStyle': 'suspense', 'RecapTextLength': 300, **({'RecapText': 'custom narration'} if custom else {'AutoGenerateRecapText': True})}
    assert payload['SpeakerConfig'] == {'AppId': 'app', 'Cluster': 'voice', 'VoiceType': 'narrator'}
    assert payload['FontConfig'] == {'Color': '#fff', 'Size': 24, 'Name': 'font'}
    assert payload['IsEraseSubtitle'] is True
    assert payload.get('DramaScriptTaskId') == ('existing-script' if custom else None)
    behavior['response'] = None
    assert client.get(f'/api/ai/aideo/drama-recap/tasks/{identifier}?persona=alice').json()['status'] == 'Processing'
    behavior['response'] = {'TaskId': 'recap-123', 'Status': 'Completed', 'Vid': 'recap-output'}
    detail = client.get(f'/api/ai/aideo/drama-recap/tasks/{identifier}?persona=alice').json()
    assert (detail['status'], detail['outputVid'], detail['dramaScriptTaskId']) == ('Completed', 'recap-output', 'script-123')
    assert len(GenerationStore(outputs).assets('alice').items) == 1
    assert client.get(f'/api/ai/aideo/tasks/{identifier}?persona=alice').json()['model'] == 'drama-recap'


@pytest.mark.parametrize('workflow,action', [('style-transfer', 'AsyncVCreativeTask'), ('drama-recap', 'CreateDramaRecapTask')])
def test_workflow_lost_submit_receipt_is_unknown_without_paid_replay(aideo, workflow, action):
    client, outputs, records, behavior, factory, base = aideo
    behavior['drop'] = True
    identifier = submit(client, workflow).json()['taskId']
    behavior['drop'] = False
    assert submit(client, workflow).json()['taskId'] == identifier
    detail = client.get(f'/api/ai/aideo/{workflow}/tasks/{identifier}?persona=alice').json()
    assert detail['state'] == 'unknown'
    assert len([row for row in records if row[0] == action]) == 1


@pytest.mark.parametrize('workflow,response,state', [
    ('style-transfer', {'Status': 'processing'}, 'running'),
    ('style-transfer', {'Status': 'failed_run', 'OutputJson': 'fixture-sk'}, 'failed'),
    ('style-transfer', {'Status': 'success', 'OutputJson': 'not-json'}, 'running'),
    ('drama-recap', {'Status': 'Failed', 'ErrorMessage': 'fixture-sk'}, 'failed'),
    ('drama-recap', {'Status': 'Completed'}, 'running'),
    ('drama-recap', {'Status': 'Completed', 'TaskId': 'wrong', 'Vid': 'output'}, 'running'),
])
def test_workflow_failure_and_incomplete_results_never_create_assets(aideo, workflow, response, state):
    client, outputs, records, behavior, factory, base = aideo
    identifier = submit(client, workflow).json()['taskId']
    behavior['response'] = response
    detail = client.get(f'/api/ai/aideo/{workflow}/tasks/{identifier}?persona=alice')
    assert detail.json()['state'] == state
    assert 'fixture-sk' not in detail.text
    assert GenerationStore(outputs).assets('alice').items == ()


@pytest.mark.parametrize('workflow', ['style-transfer', 'drama-recap'])
def test_workflow_owned_local_media_and_foreign_boundary(aideo, workflow):
    client, outputs, records, behavior, factory, base = aideo
    options = {'videoInput': 'project/clip.mp4'} if workflow == 'style-transfer' else {'vids': ['project/clip.mp4', 'barevid']}
    identifier = submit(client, workflow, **options).json()['taskId']
    assert factory().store.get('alice', identifier).state == 'running'
    foreign = submit(client, workflow, persona='bob', **options).json()['taskId']
    assert factory().store.get('bob', foreign).state == 'failed'
    assert len([row for row in records if row[0] == 'Upload']) == 1


def test_workflow_refuses_foreign_owned_output_directory(aideo):
    import json
    client, outputs, records, behavior, factory, base = aideo
    identifier = submit(client, 'style-transfer').json()['taskId']
    directory = outputs / ('AI视频-' + identifier)
    directory.mkdir()
    manifest = directory / '.easel.json'
    manifest.write_text(json.dumps({'profile': 'bob'}))
    behavior['response'] = {'Status': 'success', 'OutputJson': '{"vid":"output"}'}
    result = client.get(f'/api/ai/aideo/style-transfer/tasks/{identifier}?persona=alice').json()
    assert result['state'] == 'running'
    assert json.loads(manifest.read_text())['profile'] == 'bob'
    assert GenerationStore(outputs).assets('alice').items == ()
