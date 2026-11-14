"""Durable native video submission and restart-safe status reconciliation."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import anyio
import httpx
from pydantic import ValidationError
from PIL import Image
from typing_extensions import assert_never

from easel.content_library_models import ContentError
from easel.generation_inputs import local_image_request
from easel.generation_models import GenerationError, ImageRequest
from easel.generation_store import GenerationStore
from easel.generation_video_models import OpenAIVideoRequest, VideoJob, VideoRemixRequest, VideoTarget
from easel.generation_video_provider import NativeVideoProvider, video_base, video_setting
from easel.generation_video_preparation import prepare_video_inputs
from easel.generation_video_catalog import runtime_video_model
from easel.generation_volcengine_provider import VolcengineVideoProvider
from easel.generation_volcengine_models import VolcengineRequest
from easel.generation_volcengine_payload import normalize_volcengine
from easel.generation_video_store import VideoStore
from easel.generation_video_request import VideoGenerationRequest
from easel.ownership import OwnershipError
from easel.publish_recovery import worker_owner
from easel.remote_media import RemoteMediaError


class VideoGeneration:
    def __init__(self, outputs: Path, settings: Mapping[str, str]) -> None:
        self.store = VideoStore(GenerationStore(outputs))
        self.settings = settings

    def submit(self, payload: OpenAIVideoRequest, origin: str = '', source_id: str = '') -> VideoJob:
        if payload.input_reference:
            mapped = local_image_request(ImageRequest(persona=payload.persona, request_key=payload.request_key,
                prompt=payload.prompt, model=payload.model or 'sora-2', operation='edit', image=payload.input_reference), origin)
            payload = payload.model_copy(update={'input_reference': mapped.images[0]})
        existing = self.store.replay(payload, source_id)
        if existing is not None:
            return existing
        base = video_base(self.settings)
        model = payload.model or video_setting(self.settings, 'VIDEO_MODEL') or 'sora-2'
        model = runtime_video_model(self.store.generation.outputs, payload.persona, model)
        return self.store.enqueue(payload, VideoTarget(base=base, model=model, source_id=source_id))

    def submit_volcengine(self, payload: VolcengineRequest, origin: str = '') -> VideoJob:
        request = normalize_volcengine(payload, origin)
        existing = self.store.replay(request)
        if existing is not None:
            return existing
        provider = VolcengineVideoProvider(self.settings)
        model = runtime_video_model(self.store.generation.outputs, payload.persona, payload.model)
        return self.store.enqueue(request, VideoTarget(base=provider.base, model=model, channel='volcengine'))

    def submit_generic(self, payload: VideoGenerationRequest, origin: str = '') -> VideoJob:
        from easel.generation_video_generic_service import submit_generic
        return submit_generic(self, payload, origin)

    def remix(self, identifier: str, payload: VideoRemixRequest) -> VideoJob:
        source = self.store.get(payload.persona, identifier, 'openai')
        if not source.provider_id:
            raise GenerationError('video_source_unavailable', 409)
        request = OpenAIVideoRequest(persona=payload.persona, request_key=payload.request_key,
                                    prompt=payload.prompt, model=source.runtime_model)
        existing = self.store.replay(request, source.provider_id)
        if existing is not None:
            return existing
        if source.base_url != video_base(self.settings):
            raise GenerationError('video_provider_configuration_changed', 409)
        return self.submit(request, source_id=source.provider_id)

    async def execute(self, identifier: str = '') -> VideoJob | None:
        owner = worker_owner()
        job = self.store.claim(owner, identifier)
        if job is None:
            return None
        try:
            with anyio.fail_after(300):
                match job.channel:
                    case 'openai':
                        provider = NativeVideoProvider(self.settings)
                    case 'volcengine':
                        provider = VolcengineVideoProvider(self.settings)
                    case 'grok' | 'dashscope' | 'relay':
                        from easel.generation_video_generic_provider import GenericVideoProvider
                        provider = GenericVideoProvider(self.settings, job.channel)
                    case unreachable:
                        assert_never(unreachable)
                if provider.base != job.base_url:
                    return self.store.save(job.model_copy(update={'error': 'video_provider_configuration_changed'}), owner,
                                           'waiting')
                if job.provider_id:
                    result = await provider.status(job.provider_id)
                else:
                    prepared, inputs, imported = await anyio.to_thread.run_sync(prepare_video_inputs, self.store.generation.outputs, job, self.settings)
                    job = job.model_copy(update={'inputs': (*job.inputs, *imported)})
                    self.store.save(job, owner, 'submitting')
                    result = await provider.submit(prepared, inputs)
                job = job.model_copy(update={'provider_id': result.id, 'response': result, 'error': ''})
                self.store.save(job, owner, 'submitting')
                match result.status:
                    case 'queued' | 'in_progress':
                        return self.store.save(job, owner, 'waiting')
                    case 'failed':
                        return self.store.save(job.model_copy(update={'error': 'video_provider_failed'}), owner, 'failed')
                    case 'completed':
                        from easel.generation_volcengine_assets import archive_last_frame, localize_volcengine_result
                        frames = await archive_last_frame(self.store.generation.outputs, job)
                        job = job.model_copy(update={'additional_assets': frames})
                        asset = await provider.archive(self.store.generation.outputs, job)
                        job = localize_volcengine_result(job, asset)
                        saved = self.store.save(job, owner, 'succeeded', asset=asset)
                        from easel.generation_video_projection import project_video
                        return project_video(self.store, saved)
                    case unreachable:
                        assert_never(unreachable)
        except httpx.HTTPStatusError as error:
            state = 'waiting' if job.provider_id else 'failed' if 400 <= error.response.status_code < 500 else 'unknown'
            return self.store.save(job.model_copy(update={'error': 'video_provider_http_error'}), owner, state)
        except (httpx.RequestError, TimeoutError):
            return self.store.save(job.model_copy(update={'error': 'video_provider_unreachable'}), owner,
                                   'waiting' if job.provider_id else 'unknown')
        except RemoteMediaError:
            return self.store.save(job.model_copy(update={'error': 'video_reference_download_rejected'}), owner,
                                   'waiting' if job.provider_id else 'failed')
        except (GenerationError, OwnershipError, ContentError, ValidationError, OSError, SyntaxError, Image.DecompressionBombError) as error:
            code = error.code if isinstance(error, (GenerationError, ContentError)) else 'video_input_or_result_invalid'
            state = 'waiting' if code in {'video_configuration_required', 'video_configuration_invalid'} else 'failed'
            return self.store.save(job.model_copy(update={'error': code}), owner, state)
        except anyio.get_cancelled_exc_class():
            self.store.save(job.model_copy(update={'error': 'generation_worker_interrupted'}), owner,
                            'waiting' if job.provider_id else 'unknown')
            raise


async def dispatch_videos(factory: Callable[[], VideoGeneration]) -> None:
    from easel.generation_dispatch import dispatch_pool
    from easel.generation_queue import QueuePolicy
    service = factory()

    async def execute() -> bool:
        return await factory().execute() is not None

    await dispatch_pool(execute, service.store.recover, QueuePolicy('video', service.store.generation.outputs))
