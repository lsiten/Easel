"""Image references, real CLI transport, and metadata receipt contracts."""
import base64
import json
import os
import socket
import subprocess
import sys
from urllib.parse import quote_from_bytes

import anyio
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from easel.generation_models import ImageReceipt, ImageRequest
from easel.generation_store import GenerationStore
from easel.ownership import owned_media_path
from web import app as web
from tests.generation_support import generation as generation, payload


def test_web_edit_preserves_duplicate_references_and_mask_in_real_multipart(generation):
    _, records, _ = generation
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        response = client.post('/api/ai/image/edit', json=payload(
            image=['alice-photos/image.png', 'alice-photos/image.png'], mask='alice-photos/image.png'))
        assert response.status_code == 200 and response.json()['state'] == 'succeeded', response.text
        endpoint, _, body = records[0]
        assert endpoint == '/v1/images/edits'
        assert body.count(b'name="image[]"') == 2 and body.count(b'name="mask"') == 1


@pytest.mark.parametrize('encoding', ['base64', 'percent', 'forgiving-base64'])
def test_data_uri_edit_imports_owned_reference_and_keeps_snapshot_in_task(generation, encoding):
    outputs, records, _ = generation
    encoded = base64.b64encode((outputs / 'alice-photos/image.png').read_bytes()).decode()
    reference = 'data:image/png;base64,' + encoded
    if encoding == 'percent':
        reference = 'data:image/png,' + quote_from_bytes((outputs / 'alice-photos/image.png').read_bytes())
    if encoding == 'forgiving-base64':
        reference = 'data:image/png;base64,' + encoded.rstrip('=') + '\n'
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        result = client.post('/api/ai/image/edit', json=payload(image=reference, mask=reference)).json()
        assert result['state'] == 'succeeded' and len(records) == 1
        assert len(result['inputs']) == 1
        _, imported = owned_media_path(outputs, 'alice', result['inputs'][0]['relative_path'])
        assert imported.read_bytes() == (outputs / 'alice-photos/image.png').read_bytes()
        wire = records[0][2]
        assert b'name="image"' in wire and b'name="mask"' in wire


def test_public_url_reference_uses_real_import_transport_without_forwarding_model_key(generation, monkeypatch):
    from easel import _public_network
    outputs, records, _ = generation
    original = _public_network.resolve_public
    port = int(os.environ['IMG_BASE_URL'].split(':')[-1].split('/')[0])
    def resolve(host, requested_port):
        if host == 'fixture.example':
            return (_public_network.PublicAddress(socket.AF_INET, socket.SOCK_STREAM, 6, ('127.0.0.1', port)),)
        return original(host, requested_port)
    monkeypatch.setattr(_public_network, 'resolve_public', resolve)
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        result = client.post('/api/ai/image/edit', json=payload(image=f'http://fixture.example:{port}/source.png')).json()
        assert result['state'] == 'succeeded', result
        assert len(records) == 2 and records[0][0] == '/source.png' and records[0][1] is None
        assert records[1][0] == '/v1/images/edits'
        assert len(result['inputs']) == 1
        owned_media_path(outputs, 'alice', result['inputs'][0]['relative_path'])


def test_private_url_reference_is_rejected_without_contacting_input_or_model(generation):
    _, records, _ = generation
    reference = os.environ['IMG_BASE_URL'].removesuffix('/v1') + '/source.png'
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        result = client.post('/api/ai/image/edit', json=payload(image=reference)).json()
        assert result['state'] == 'failed' and result['error'] == 'remote_media_download_rejected'
    assert not records


def test_own_media_url_is_resolved_locally_and_enforces_persona(generation):
    _, records, _ = generation
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        result = client.post('/api/ai/image/edit', json=payload(image='http://localhost:7860/api/media/alice-photos/image.png')).json()
        assert result['state'] == 'succeeded' and len(records) == 1
        assert records[0][0] == '/v1/images/edits'
        rejected = client.post('/api/ai/image/edit', json=payload(request_key='foreign-url', image='/api/media/bob-photos/image.png'))
        assert rejected.status_code == 403 and len(records) == 1
        invalid = client.post('/api/ai/image/edit', json=payload(request_key='invalid-url', image='/api/media/alice-photos/%00.png'))
        assert invalid.status_code == 400 and len(records) == 1


def test_async_provider_image_saver_preserves_available_metadata_receipt(generation, monkeypatch):
    from easel import _public_network
    from skills.shared.scripts import ai_image, output_paths
    outputs, records, _ = generation
    original = _public_network.resolve_public
    port = int(os.environ['IMG_BASE_URL'].split(':')[-1].split('/')[0])
    def resolve(host, requested_port):
        if host == 'fixture.example':
            return (_public_network.PublicAddress(socket.AF_INET, socket.SOCK_STREAM, 6, ('127.0.0.1', port)),)
        return original(host, requested_port)
    monkeypatch.setattr(_public_network, 'resolve_public', resolve)
    monkeypatch.setattr(output_paths, 'PROJECT_ROOT', outputs.parent)
    monkeypatch.setattr(output_paths, 'OUTPUTS_DIR', outputs)
    receipt_path = outputs / '_generation/receipts/async-image.json'
    monkeypatch.setenv('EASEL_IMAGE_RECEIPT', str(receipt_path))
    task = {'created': 1730000000, 'usage': {'total_tokens': 11},
            'private': 'private-async-receipt', 'result': {
                'output_format': 'png', 'images': [{'url': [f'http://fixture.example:{port}/source.png'],
                                                  'revised_prompt': 'async-refined'}]}}
    files = ai_image._save_async_images(task, str(outputs / '异步供应商校验/image.png'), 'png')
    with Image.open(files[0]) as picture:
        assert picture.size == (3, 2)
    receipt = ImageReceipt.model_validate_json(receipt_path.read_bytes())
    assert receipt.usage.total_tokens == 11 and receipt.revised_prompts == ('async-refined',)
    assert receipt.output_format == 'png' and receipt.created == 1730000000
    assert 'private-async-receipt' not in receipt_path.read_text() and len(records) == 1


def test_edit_input_changed_after_queueing_is_rejected_before_provider_call(generation):
    outputs, records, _ = generation
    service = web._image_generation()
    job = service.submit(ImageRequest(**payload(operation='edit', image='alice-photos/image.png')))
    (outputs / 'alice-photos/image.png').write_bytes(b'changed')
    result = anyio.run(service.execute, job.id)
    assert result.state == 'failed' and result.error == 'generation_input_changed'
    assert not records and not service.store.assets('alice').items


@pytest.mark.parametrize('base', ['https://api.apimart.ai/v1', 'https://api.agnes-ai.cn/v1'])
def test_unsupported_provider_mask_is_rejected_before_queuing_or_billing(generation, monkeypatch, base):
    outputs, records, _ = generation
    monkeypatch.setenv('IMG_BASE_URL', base)
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        result = client.post('/api/ai/image/edit', json=payload(image='alice-photos/image.png', mask='alice-photos/image.png'))
        assert result.status_code == 400 and result.json()['detail'] == 'generation_mask_unsupported'
        assert GenerationStore(outputs).list('alice').total == 0
    assert not records


def test_direct_async_image_cli_rejects_mask_before_any_provider_request(generation):
    outputs, records, _ = generation
    photo = outputs / 'alice-photos/image.png'
    target = outputs / 'CLI编辑校验/generated.png'
    process = subprocess.run([sys.executable, str(web.PROJECT_ROOT / 'skills/shared/scripts/ai_image.py'),
        'img2img', '--prompt', '保留遮罩外区域', '--image', str(photo), '--mask', str(photo),
        '--mode', 'async', '-o', str(target)], env={**os.environ,
        'IMG_MODEL': 'provider-image', 'EASEL_ROOT': str(outputs.parent)},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    assert process.returncode == 1 and '--mask' in process.stderr
    assert not records and not target.exists()


def test_api_prompt_that_looks_like_cli_flag_is_delivered_literally(generation):
    _, records, _ = generation
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        result = client.post('/api/ai/image/generate', json=payload(prompt='--help')).json()
        assert result['state'] == 'succeeded' and json.loads(records[0][2])['prompt'] == '--help'


def test_nul_text_is_rejected_without_queueing_or_stopping_app(generation):
    outputs, records, _ = generation
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        result = client.post('/api/ai/image/generate', json=payload(prompt='invalid\0text'))
        assert result.status_code == 422 and GenerationStore(outputs).list('alice').total == 0
        assert client.get('/api/ai/logs?persona=alice').status_code == 200
    assert not records


@pytest.mark.skipif(os.name == 'nt', reason='Windows filenames do not support line breaks')
def test_owned_image_filename_with_line_break_keeps_its_original_bytes(generation):
    outputs, records, _ = generation
    source = outputs / 'alice-photos/image.png'
    (outputs / 'alice-photos/line\nbreak.png').write_bytes(source.read_bytes())
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
        result = client.post('/api/ai/image/edit', json=payload(image='alice-photos/line\nbreak.png')).json()
        assert result['state'] == 'succeeded' and len(records) == 1
        assert result['inputs'][0]['relative_path'] == 'alice-photos/line\nbreak.png'
