"""Frozen input capability boundaries, multimodal translation and projection retry."""
import json
import time

from easel.content_library import ContentLibrary
from easel.content_library_models import ContentScope, GroupInput
from tests.generic_video_support import generic as generic, body


def test_grok_edit_uses_frozen_video_grant_and_expiry_hash_and_symlink_guards(generic):
    client, service, outputs, _, records, _ = generic
    result = client.post('/api/ai/video/generations', json=body('grok', video_url='/api/media/photos/clip.mp4'))
    assert result.status_code == 200, result.text
    assert records[0][1] == '/v1/videos/edits'
    wire = json.loads(records[0][3])
    assert set(wire) == {'model', 'prompt', 'video'}
    endpoint = wire['video']['url'].replace('https://easel.example', '')
    original = (outputs / 'photos/clip.mp4').read_bytes()
    assert client.get(endpoint).content == original
    assert client.get(endpoint[:-1] + ('a' if endpoint[-1] != 'a' else 'b')).status_code == 403
    frozen = next((outputs / '_publish/alice' / result.json()['id'] / 'media').glob('*.mp4'))
    frozen.chmod(0o600)
    frozen.write_bytes(b'changed')
    assert client.get(endpoint).status_code == 409
    frozen.unlink()
    frozen.symlink_to(outputs / 'photos/clip.mp4')
    assert client.get(endpoint).status_code == 409
    frozen.unlink()
    frozen.write_bytes(original)
    with service.store.generation.connect() as db:
        db.execute('UPDATE video_media_grants SET expires_at=?', (time.time() - 1,))
    assert client.get(endpoint).status_code == 403
    assert len(records) == 1


def test_foreign_reference_and_unsupported_fields_are_rejected_before_paid_post(generic):
    client, _, _, _, records, _ = generic
    for payload in (body('grok', persona='bob', image='photos/image.png'),
                    body('dashscope', image='photos/image.png', image_tail='photos/image.png'),
                    body('openai', image=['photos/image.png']),
                    body('grok', audios=['photos/clip.mp4']),
                    body('relay', video_url='../../outside.mp4')):
        response = client.post('/api/ai/video/generations', json=payload)
        assert response.status_code in {400, 403}, response.text
    assert records == []


def test_volcengine_generic_tail_and_multimodal_roles_preserve_native_payload(generic):
    client, _, _, _, records, _ = generic
    tail = client.post('/api/ai/video/generations', json=body('volcengine', image='photos/image.png',
        image_tail='photos/image.png', duration=3, seed=42, watermark=True, tools=[{'type': 'web_search'}]))
    assert tail.status_code == 200, tail.text
    wire = json.loads(records[0][3])
    assert [entry.get('role') for entry in wire['content']] == ['first_frame', 'last_frame', None]
    assert wire['duration'] == 3 and wire['seed'] == 42 and wire['watermark'] is True
    assert wire['tools'] == [{'type': 'web_search'}]
    multi = client.post('/api/ai/video/generations', json=body('volcengine', request_key='multi', image=['photos/image.png'],
        images=['photos/image.png'], videos=['photos/clip.mp4'], audios=['photos/clip.mp4'], ratio='16:9'))
    assert multi.status_code == 200, multi.text
    wire = json.loads(records[1][3])
    assert [entry.get('role') for entry in wire['content']] == ['reference_image', 'reference_image', 'reference_video', 'reference_audio', None]
    assert wire['content'][2]['video_url']['url'].startswith('https://easel.example/api/ai/video/media/')
    assert wire['content'][3]['audio_url']['url'].startswith('https://easel.example/api/ai/video/media/')


def test_success_remains_accessible_when_group_projection_fails_and_read_retries_once(generic):
    client, _, outputs, _, records, _ = generic
    library = ContentLibrary(outputs)
    scope = ContentScope(persona='alice', domain='media')
    group = library.create_group(scope, GroupInput(name='Temporary', kind='video'))
    created = client.post('/api/ai/video/generations', json=body(groupId=group.id)).json()
    library.delete_group(scope, group.id)
    endpoint = '/api/ai/video/generations/' + created['id']
    first = client.get(endpoint, params={'persona': 'alice'}).json()
    assert first['status'] == 'SUCCESS' and first['videoUrl'] and first['mediaId'] is None
    assert first['mediaError'] == 'group_not_found'
    with library.connect() as db:
        db.execute('INSERT INTO content_groups VALUES(?,?,?,?,?)', (group.id, scope.persona, scope.domain, 0, group.model_dump_json()))
    recovered = client.get(endpoint, params={'persona': 'alice'}).json()
    assert recovered['status'] == 'SUCCESS' and recovered['mediaId'] and recovered['mediaError'] is None
    assert client.get(endpoint, params={'persona': 'alice'}).json()['mediaId'] == recovered['mediaId']
    assert sum(row[0] == 'POST' for row in records) == 1


def test_relay_cover_is_validated_archived_and_indexed_as_owned_image(generic):
    from PIL import Image
    client, service, outputs, _, records, behavior = generic
    behavior['cover'] = True
    created = client.post('/api/ai/video/generations', json=body()).json()
    result = client.get('/api/ai/video/generations/' + created['id'], params={'persona': 'alice'}).json()
    assert result['status'] == 'SUCCESS' and result['coverUrl'].startswith('/api/media/')
    job = service.store.get('alice', created['id'])
    with Image.open(outputs / job.additional_assets[0].path) as frame:
        assert frame.size == (3, 2)
    with service.store.generation.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM generation_assets WHERE job_id=?', (job.id,)).fetchone()[0] == 2
    assert next(row for row in records if row[1] == '/cover.png')[2].get('x-api-key') is None


def test_inline_audio_reference_is_forwarded_as_base64_and_invalid_data_never_posts(generic):
    import base64
    import io
    import wave
    client, _, _, _, records, _ = generic
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setparams((1, 2, 8000, 800, 'NONE', 'not compressed'))
        audio.writeframes(b'\0\0' * 800)
    inline = 'data:audio/wav;base64,' + base64.b64encode(stream.getvalue()).decode()
    response = client.post('/api/ai/video/generations', json=body('volcengine', image=['photos/image.png'], audios=[inline]))
    assert response.status_code == 200 and response.json()['status'] == 'SUBMITTED', response.text
    assert json.loads(records[0][3])['content'][1]['audio_url']['url'] == inline
    invalid = client.post('/api/ai/video/generations', json=body('volcengine', request_key='bad', image=['photos/image.png'], audios=['data:audio/wav;base64,!!!!']))
    assert invalid.json()['status'] == 'FAILURE' and len(records) == 1
