"""Native scene normalization and frozen image references for Ark video tasks."""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
import re

from typing_extensions import assert_never

from easel.generation_inputs import external_reference, local_image_request, prepare_image_inputs
from easel.generation_models import GenerationError, GenerationJob, ImageRequest
from easel.generation_video_models import OpenAIVideoRequest, VideoJob
from easel.generation_volcengine_models import (VideoAudio, VideoImage, VideoReference, VideoText,
                                               VideoURL, VolcengineRequest)
from easel.ownership import MediaManifestEntry, owned_media_path
from easel.remote_media import validate_public_url


def normalize_volcengine(request: VolcengineRequest, origin: str) -> VolcengineRequest:
    content = []
    inline: dict[str, str | int | bool] = {}
    names = {'rs': 'resolution', 'rt': 'ratio', 'dur': 'duration', 'seed': 'seed', 'wm': 'watermark'}
    for item in request.content:
        match item:
            case VideoText():
                parts = re.split(r'\s+--\w+', item.text, maxsplit=1)
                text = parts[0].strip()
                command = item.text[len(parts[0]):] if len(parts) > 1 else ''
                parsed: dict[str, str | int | bool] = {}
                for short, value in re.findall(r'--([a-z]+)\s+(\S+)', command, re.IGNORECASE):
                    name = names.get(short.lower())
                    if name:
                        if name in {'duration', 'seed'}:
                            number = re.match(r'^[+-]?\d+', value)
                            if number is None:
                                raise GenerationError('video_inline_parameter_invalid')
                            parsed[name] = int(number[0])
                        else:
                            parsed[name] = value.lower() == 'true' if name == 'watermark' else value
                for name, value in parsed.items():
                    inline.setdefault(name, value)
                content.append(item.model_copy(update={'text': text}))
            case VideoImage():
                mapped = local_image_request(ImageRequest(persona=request.persona, request_key=request.request_key,
                    model=request.model, prompt='video reference', operation='edit', image=item.image_url.url), origin)
                content.append(item.model_copy(update={'image_url': VideoURL(url=mapped.images[0])}))
            case VideoReference() | VideoAudio():
                content.append(item)
            case unreachable:
                assert_never(unreachable)
    values = request.model_dump()
    values['content'] = tuple(item.model_dump() for item in content)
    for name, value in inline.items():
        if values[name] is None:
            values[name] = value
    return VolcengineRequest.model_validate(values)


def volcengine_local_paths(request: VolcengineRequest) -> tuple[str, ...]:
    return tuple(item.image_url.url for item in request.content
                 if isinstance(item, VideoImage) and not external_reference(item.image_url.url))


def canonical_volcengine(request: VolcengineRequest, outputs: Path) -> VolcengineRequest:
    canonical = {path: owned_media_path(outputs, request.persona, path)[1].relative_to(outputs).as_posix()
                 for path in volcengine_local_paths(request)}
    content = tuple(item.model_copy(update={'image_url': VideoURL(url=canonical.get(item.image_url.url, item.image_url.url))})
                    if isinstance(item, VideoImage) else item for item in request.content)
    return request.model_copy(update={'content': content})


def prepare_volcengine_inputs(outputs: Path, job: VideoJob) -> tuple[VolcengineRequest, tuple[MediaManifestEntry, ...]]:
    match job.request:
        case VolcengineRequest() as request:
            pass
        case OpenAIVideoRequest():
            raise GenerationError('video_protocol_invalid')
        case unreachable:
            assert_never(unreachable)
    images = tuple(item.image_url.url for item in request.content if isinstance(item, VideoImage))
    imported: tuple[MediaManifestEntry, ...] = ()
    replacements: list[str] = []
    if images:
        image_job = GenerationJob(id=job.id, persona=job.persona, state='running', inputs=job.inputs,
            request=ImageRequest(persona=job.persona, request_key=request.request_key, model=job.runtime_model,
                prompt='video reference', operation='edit', image=images),
            created_at=job.created_at, updated_at=job.updated_at)
        frozen, imported = prepare_image_inputs(outputs, image_job)
        for path in frozen:
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            replacements.append('data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode())
    content = []
    cursor = 0
    for item in request.content:
        match item:
            case VideoImage():
                content.append(item.model_copy(update={'image_url': VideoURL(url=replacements[cursor])}))
                cursor += 1
            case VideoReference():
                validate_public_url(item.video_url.url)
                content.append(item)
            case VideoAudio():
                if not item.audio_url.url.lower().startswith('data:audio/'):
                    validate_public_url(item.audio_url.url)
                content.append(item)
            case VideoText():
                content.append(item)
            case unreachable:
                assert_never(unreachable)
    return request.model_copy(update={'content': tuple(content)}), imported
