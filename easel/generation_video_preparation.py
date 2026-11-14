"""Prepare only the reference variant belonging to the selected native protocol."""
from pathlib import Path
from collections.abc import Mapping
from easel.generation_video_request import VideoGenerationRequest

from typing_extensions import assert_never

from easel.generation_inputs import prepare_image_inputs
from easel.generation_models import GenerationJob, ImageRequest
from easel.generation_video_models import OpenAIVideoRequest, VideoJob
from easel.generation_volcengine_models import VolcengineRequest
from easel.generation_volcengine_payload import prepare_volcengine_inputs
from easel.ownership import MediaManifestEntry


def prepare_video_inputs(outputs: Path, job: VideoJob, settings: Mapping[str, str] | None = None) -> tuple[VideoJob, tuple[Path, ...], tuple[MediaManifestEntry, ...]]:
    match job.request:
        case OpenAIVideoRequest() as request:
            image_request = ImageRequest(persona=job.persona, request_key=request.request_key,
                prompt=request.prompt, model=job.runtime_model, operation='edit' if request.input_reference else 'generate',
                image=request.input_reference if request.input_reference else ())
            image_job = GenerationJob(id=job.id, persona=job.persona, state='running', request=image_request,
                inputs=job.inputs, created_at=job.created_at, updated_at=job.updated_at)
            references, imported = prepare_image_inputs(outputs, image_job)
            return job, references, imported
        case VolcengineRequest():
            prepared, imported = prepare_volcengine_inputs(outputs, job)
            return job.model_copy(update={'request': prepared}), (), imported
        case VideoGenerationRequest():
            from easel.generation_video_generic_inputs import prepare_generic
            return prepare_generic(outputs, job, settings or {})
        case unreachable:
            assert_never(unreachable)
