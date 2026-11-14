"""Owned generic video catalog, create, detail/history and signed media delivery."""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import math
from easel.content_library_models import ContentError
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from easel.generation_router import generation_errors
from easel.generation_video_catalog import video_models
from easel.generation_video_grants import VideoMediaGrants
from easel.generation_video_models import VideoJob, OpenAIVideoRequest
from easel.generation_volcengine_models import VolcengineRequest, VideoImage, VideoText, VideoReference, VideoAudio
from typing_extensions import assert_never
from easel.generation_video_projection import project_video
from easel.generation_video_request import VideoGenerationRequest
from easel.generation_videos import VideoGeneration


@contextmanager
def video_errors() -> Iterator[None]:
    try:
        with generation_errors():
            yield
    except ContentError as error:
        raise HTTPException(error.status, error.code) from error


def common_response(job: VideoJob):
    match job.request:
        case VideoGenerationRequest() as request:
            inputs = request.model_dump(mode='json', by_alias=True, exclude={'persona', 'request_key', 'model'})
            inputs.update(videoUrl=request.video_url or None, aspectRatio=request.ratio or request.metadata.get('aspectRatio'))
            group = request.group_id
        case OpenAIVideoRequest() as request:
            inputs = {'prompt': request.prompt, 'image': request.input_reference}
            group = ''
        case VolcengineRequest() as request:
            images = [item.image_url.url for item in request.content if isinstance(item, VideoImage)]
            first = next((item.image_url.url for item in request.content if isinstance(item, VideoImage) and item.role in {None, 'first_frame'}), '')
            inputs = {'prompt': next((item.text for item in request.content if isinstance(item, VideoText)), ''),
                'image': first or (images[0] if len(images) == 1 else images), 'images': images,
                'videos': [item.video_url.url for item in request.content if isinstance(item, VideoReference)],
                'audios': [item.audio_url.url for item in request.content if isinstance(item, VideoAudio)],
                'duration': request.duration, 'resolution': request.resolution, 'aspectRatio': request.ratio, 'watermark': request.watermark}
            group = ''
        case unreachable:
            assert_never(unreachable)
    statuses = {'queued': 'SUBMITTED', 'submitting': 'InProgress', 'waiting': 'InProgress',
                'succeeded': 'SUCCESS', 'failed': 'FAILURE', 'unknown': 'UNKNOWN'}
    return {'id': job.id, 'model': job.request.model or job.runtime_model, 'channel': job.channel,
        'input': inputs, 'status': statuses[job.state], 'state': job.state, 'submittedAt': datetime.fromtimestamp(job.created_at, timezone.utc).isoformat(),
        'startedAt': datetime.fromtimestamp(job.created_at, timezone.utc).isoformat(), 'finishedAt': datetime.fromtimestamp(job.updated_at, timezone.utc).isoformat() if job.state in {'succeeded', 'failed'} else None,
        'videoUrl': '/api/media/' + quote(job.asset.path, safe='/') if job.asset else None,
        'coverUrl': '/api/media/' + quote(job.additional_assets[0].path, safe='/') if job.additional_assets else None,
        'mediaId': job.media_id or None, 'groupId': group or None,
        'mediaError': job.media_error or None, 'error': {'message': job.error} if job.error else None}


def create_router(factory: Callable[[], VideoGeneration], profile_valid: Callable[[str], bool],
                  require_access: Callable[[Request], None]) -> APIRouter:
    router = APIRouter(prefix='/api/ai', tags=['creation'])

    def owner(persona: str, request: Request) -> None:
        if request.method != 'GET' or not getattr(request.state, 'caller_key', None):
            require_access(request)
        if not profile_valid(persona):
            raise HTTPException(400, 'persona_invalid')

    @router.get('/models/video/generation')
    def models(persona: str, request: Request):
        owner(persona, request)
        with video_errors():
            service = factory()
            return video_models(service.store.generation.outputs, persona, service.settings)

    @router.post('/video/generations')
    async def create(payload: VideoGenerationRequest, request: Request):
        owner(payload.persona, request)
        with video_errors():
            service = factory()
            job = service.submit_generic(payload, str(request.base_url))
            if job.state == 'queued':
                await service.execute(job.id)
            result = common_response(project_video(service.store, service.store.get(job.persona, job.id)))
            if result['status'] == 'InProgress':
                result['status'] = 'SUBMITTED'
            return result

    @router.get('/video/generations')
    def history(persona: str, request: Request, page: int = 1, pageSize: int = 10, page_size: int | None = None):
        owner(persona, request)
        with video_errors():
            service = factory()
            size = page_size if page_size is not None else pageSize
            result = service.store.list(persona, page, size)
            items = tuple(common_response(project_video(service.store, service.store.get(persona, item.id))) for item in result.items)
            return {'list': items, 'items': items, 'total': result.total, 'page': page, 'page_size': size,
                    'pageSize': size, 'totalPages': math.ceil(result.total / size)}

    @router.get('/video/generations/{identifier}')
    async def detail(identifier: str, persona: str, request: Request):
        owner(persona, request)
        with video_errors():
            service = factory()
            job = service.store.get(persona, identifier)
            if job.state == 'waiting':
                await service.execute(job.id)
            return common_response(project_video(service.store, service.store.get(persona, identifier)))

    @router.get('/video/media/{token}')
    def media(token: str):
        with video_errors():
            path = VideoMediaGrants(factory().store.generation).resolve(token)
            return FileResponse(path, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    return router
