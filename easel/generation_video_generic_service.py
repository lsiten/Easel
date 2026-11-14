"""Resolve and freeze generic intent before entering the durable native queue."""
from typing import TYPE_CHECKING
from typing_extensions import assert_never

from easel.generation_models import GenerationError
from easel.generation_video_catalog import video_models
from easel.generation_video_generic_inputs import normalize_generic
from easel.generation_video_generic_payload import validate_channel_fields
from easel.generation_video_generic_provider import GenericVideoProvider
from easel.generation_video_models import VideoJob, VideoTarget
from easel.generation_video_options import VideoCapabilities
from easel.generation_video_projection import validate_video_group
from easel.generation_video_provider import NativeVideoProvider
from easel.generation_video_request import VideoGenerationRequest
from easel.generation_volcengine_provider import VolcengineVideoProvider
from easel.local_user import LocalUsers

if TYPE_CHECKING:
    from easel.generation_videos import VideoGeneration


def submit_generic(service: 'VideoGeneration', payload: VideoGenerationRequest, origin: str) -> VideoJob:
    request = normalize_generic(payload, origin)
    existing = service.store.replay(request)
    if existing is not None:
        return existing
    outputs = service.store.generation.outputs
    validate_video_group(outputs, request)
    matching = [item for item in video_models(outputs, request.persona, service.settings)
                if item.name == request.model]
    if not matching:
        raise GenerationError('video_model_invalid')
    candidates = [item for item in matching if item.configured]
    if not candidates:
        raise GenerationError('video_provider_configuration_required', 409)
    channel = candidates[0].channel
    selected = LocalUsers(outputs).read(request.persona).ai.video
    options = (VideoCapabilities.model_validate(selected.options)
               if selected is not None and selected.default_model == request.model
               and VideoCapabilities.model_validate(selected.options).channel == channel
               else VideoCapabilities.model_validate(candidates[0].model_dump()))
    model, effective = options.resolve(request)
    validate_channel_fields(effective, channel)
    match channel:
        case 'openai':
            provider = NativeVideoProvider(service.settings)
        case 'volcengine':
            provider = VolcengineVideoProvider(service.settings)
        case 'grok' | 'dashscope' | 'relay':
            provider = GenericVideoProvider(service.settings, channel)
        case unreachable:
            assert_never(unreachable)
    return service.store.enqueue(request, VideoTarget(base=provider.base, model=model, channel=channel, effective_request=effective))
