"""Explicit translations of generic requests into the five source protocols."""
from pydantic import JsonValue, TypeAdapter
from typing import Final, Literal
from typing_extensions import assert_never

from easel.generation_models import GenerationError
from easel.generation_video_models import OpenAIVideoRequest
from easel.generation_video_request import VideoChannel, VideoGenerationRequest
from easel.generation_volcengine_models import (VideoAudio, VideoImage, VideoReference, VideoText,
                                               VideoURL, VolcengineRequest)
from easel.generation_volcengine_payload import normalize_volcengine


DASHSCOPE_MODE: Final = TypeAdapter(Literal['text2video', 'image2video', 'multi-image2video', 'video2video'])


def native_request(request: VideoGenerationRequest, channel: VideoChannel) -> OpenAIVideoRequest | VolcengineRequest:
    match channel:
        case 'openai':
            if isinstance(request.image, tuple) and request.image:
                raise GenerationError('video_openai_single_image_required')
            return OpenAIVideoRequest(persona=request.persona, request_key=request.request_key, model=request.model,
                prompt=request.prompt, input_reference=request.image if isinstance(request.image, str) else '',
                seconds=format(request.duration, 'g') if request.duration is not None else '', size=request.size)
        case 'volcengine':
            primary = request.image if isinstance(request.image, str) else ''
            references = (*(() if isinstance(request.image, str) else request.image), *request.images)
            videos = (*((request.video_url,) if request.video_url else ()), *request.videos)
            first = bool(primary) and (not (references or videos or request.audios) or bool(request.image_tail))
            content = []
            if first:
                content.append(VideoImage(image_url=VideoURL(url=primary), role='first_frame'))
            if request.image_tail:
                if not primary:
                    raise GenerationError('video_tail_requires_first_frame')
                if references:
                    raise GenerationError('video_reference_scene_conflict')
                content.append(VideoImage(image_url=VideoURL(url=request.image_tail), role='last_frame'))
            else:
                content.extend(VideoImage(image_url=VideoURL(url=value), role='reference_image')
                               for value in (*references, *((primary,) if primary and not first else ())))
            content.extend(VideoReference(video_url=VideoURL(url=value), role='reference_video') for value in videos)
            content.extend(VideoAudio(audio_url=VideoURL(url=value), role='reference_audio') for value in request.audios)
            content.append(VideoText(text=request.prompt))
            ratio = request.ratio or request.metadata.get('aspectRatio')
            return normalize_volcengine(VolcengineRequest(persona=request.persona, request_key=request.request_key,
                model=request.model, content=tuple(content), resolution=request.resolution or request.size or None,
                ratio=ratio or None, duration=request.duration, seed=request.seed, watermark=request.watermark,
                tools=request.tools or None), '')
        case 'grok' | 'dashscope' | 'relay':
            raise GenerationError('video_protocol_invalid')
        case unreachable:
            assert_never(unreachable)


def validate_channel_fields(request: VideoGenerationRequest, channel: VideoChannel) -> None:
    """Reject unsupported nonempty fields before a paid call, never silently discard them."""
    match channel:
        case 'relay':
            return
        case 'volcengine':
            native_request(request, channel)
            return
        case 'openai':
            unsupported = ('images', 'image_tail', 'video_url', 'videos', 'audios', 'resolution', 'ratio', 'seed', 'watermark', 'tools')
            native_request(request, channel)
        case 'grok':
            unsupported = ('image_tail', 'videos', 'audios', 'size', 'seed', 'watermark', 'tools')
            if request.video_url and (request.primary_images() or request.images):
                raise GenerationError('video_edit_reference_conflict')
        case 'dashscope':
            unsupported = ('image_tail', 'audios', 'size', 'tools')
            if len(request.videos) + bool(request.video_url) > 1:
                raise GenerationError('video_single_video_required')
            images = request.primary_images() + request.images
            if request.mode == 'image2video' and len(images) != 1 or request.mode == 'multi-image2video' and not images:
                raise GenerationError('video_image_count_invalid')
            if request.mode == 'text2video' and images or request.mode == 'video2video' and not (request.video_url or request.videos):
                raise GenerationError('video_mode_reference_conflict')
            if request.mode not in {'text2video', 'image2video', 'multi-image2video', 'video2video'}:
                raise GenerationError('video_mode_invalid')
        case unreachable:
            assert_never(unreachable)
    if any(getattr(request, field) not in ('', (), None) for field in unsupported):
        raise GenerationError('video_channel_field_unsupported')


def json_payload(request: VideoGenerationRequest, channel: VideoChannel, model: str) -> dict[str, JsonValue]:
    match channel:
        case 'relay':
            return {**request.model_dump(mode='json', exclude_none=True, exclude={'persona', 'request_key', 'group_id'}), 'model': model}
        case 'grok':
            payload: dict[str, JsonValue] = {'model': model, 'prompt': request.prompt}
            if request.video_url:
                return {**payload, 'video': {'url': request.video_url}}
            values = {'duration': request.duration, 'aspect_ratio': request.ratio or request.metadata.get('aspectRatio'),
                      'resolution': request.resolution or request.metadata.get('resolution')}
            payload.update({key: value for key, value in values.items() if value is not None and value != ''})
            if isinstance(request.image, str) and request.image:
                payload['image'] = {'url': request.image}
            refs = (*(() if isinstance(request.image, str) else request.image), *request.images)
            if refs:
                payload['reference_images'] = [{'url': value} for value in refs]
            return payload
        case 'dashscope':
            parameters: dict[str, JsonValue] = {'watermark': request.watermark if request.watermark is not None else False}
            if request.resolution:
                parameters['resolution'] = request.resolution
            if request.seed is not None:
                parameters['seed'] = request.seed
            images = request.primary_images() + request.images
            media = []
            mode = DASHSCOPE_MODE.validate_python(request.mode)
            match mode:
                case 'video2video':
                    media = [{'type': 'video', 'url': request.video_url or request.videos[0]}]
                    media.extend({'type': 'reference_image', 'url': value} for value in images)
                case 'text2video' | 'image2video' | 'multi-image2video':
                    media = [{'type': 'first_frame' if request.mode == 'image2video' else 'reference_image', 'url': value} for value in images]
                    if request.duration is not None:
                        parameters['duration'] = request.duration
                    if request.ratio and request.mode != 'image2video':
                        parameters['ratio'] = request.ratio
                case unreachable:
                    assert_never(unreachable)
            inputs: dict[str, JsonValue] = {'prompt': request.prompt}
            if media:
                inputs['media'] = media
            return {'model': model, 'input': inputs, 'parameters': parameters}
        case 'openai' | 'volcengine':
            raise GenerationError('video_protocol_invalid')
        case unreachable:
            assert_never(unreachable)
