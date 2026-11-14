"""Owned generic references frozen before submission, with capability URLs for AV."""
import base64
import binascii
from collections.abc import Mapping
import mimetypes
from pathlib import Path

from PIL import Image
from typing_extensions import assert_never

from easel.channels.media_metadata import inspect_local_media
from easel.generation_inputs import external_reference, local_image_request, prepare_image_inputs
from easel.generation_models import GenerationError, GenerationJob, ImageRequest
from easel.generation_store import GenerationStore
from easel.generation_video_grants import VideoMediaGrants
from easel.generation_video_models import VideoJob
from easel.generation_video_request import VideoGenerationRequest
from easel.ownership import MediaManifestEntry, freeze_media
from easel.remote_media import validate_public_url


def normalize_generic(request: VideoGenerationRequest, origin: str) -> VideoGenerationRequest:
    refs = tuple(item.value for item in request.references())
    if not refs:
        return request
    mapped = local_image_request(ImageRequest(persona=request.persona, request_key=request.request_key,
        model=request.model, prompt=request.prompt, operation='edit', image=refs), origin)
    return request.replace_references(dict(zip(refs, mapped.images)))


def prepare_generic(outputs: Path, job: VideoJob, settings: Mapping[str, str]) -> tuple[VideoJob, tuple[Path, ...], tuple[MediaManifestEntry, ...]]:
    request = job.effective_request
    if request is None:
        raise GenerationError('video_protocol_invalid')
    frozen = freeze_media(outputs, job.persona, job.id, job.inputs)
    staged = {entry.relative_path: (path, entry) for entry, path in zip(job.inputs, frozen)}
    replacements = {}
    images = tuple(item.value for item in request.references() if item.kind == 'image')
    image_paths: tuple[Path, ...] = ()
    imported: tuple[MediaManifestEntry, ...] = ()
    if images:
        image_job = GenerationJob(id=job.id, persona=job.persona, state='running', inputs=job.inputs,
            request=ImageRequest(persona=job.persona, request_key=request.request_key, model=job.runtime_model,
                prompt=request.prompt, operation='edit', image=images), created_at=job.created_at, updated_at=job.updated_at)
        image_paths, imported = prepare_image_inputs(outputs, image_job)
        for reference, path in zip(images, image_paths):
            with Image.open(path) as picture:
                picture.verify()
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            replacements[reference] = 'data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode()
    grants = VideoMediaGrants(GenerationStore(outputs))
    for reference in request.references():
        if reference.kind == 'image':
            continue
        if reference.kind == 'audio' and reference.value.lower().startswith('data:audio/'):
            header, separator, encoded = reference.value.partition(',')
            if not separator or not header.lower().endswith(';base64') or len(encoded) > 70 * 1024**2:
                raise GenerationError('video_audio_data_invalid')
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except binascii.Error as error:
                raise GenerationError('video_audio_data_invalid') from error
            if not decoded or len(decoded) > 50 * 1024**2:
                raise GenerationError('video_audio_data_invalid')
            continue
        if external_reference(reference.value):
            validate_public_url(reference.value)
            continue
        path, entry = staged[reference.value]
        metadata = inspect_local_media(path)
        if not metadata.duration_sec or reference.kind == 'video' and not (metadata.width and metadata.height):
            raise GenerationError('video_reference_invalid')
        replacements[reference.value] = grants.create(job, (path, entry), settings.get('EASEL_CHANNEL_MEDIA_BASE_URL', ''))
    prepared = request.replace_references(replacements)
    from easel.generation_video_generic_payload import native_request
    match job.channel:
        case 'openai' | 'volcengine':
            native = native_request(prepared, job.channel)
            return job.model_copy(update={'request': native, 'effective_request': prepared}), image_paths, imported
        case 'grok' | 'dashscope' | 'relay':
            return job.model_copy(update={'effective_request': prepared}), (), imported
        case unreachable:
            assert_never(unreachable)
