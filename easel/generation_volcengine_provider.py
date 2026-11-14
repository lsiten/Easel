"""Ark native protocol sharing owned video archival and credential-safe HTTP."""
from collections.abc import Mapping
from pathlib import Path
import hashlib

import httpx
from typing_extensions import assert_never

from easel.generation_models import GenerationError
from easel.generation_video_models import OpenAIVideoRequest, ProviderVideo, ProviderVideoError, VideoJob
from easel.generation_video_provider import NativeVideoProvider, video_setting
from easel.generation_volcengine_models import VolcengineRequest, VolcengineResult


class VolcengineVideoProvider(NativeVideoProvider):
    def __init__(self, settings: Mapping[str, str]) -> None:
        super().__init__({'VIDEO_BASE_URL': video_setting(settings, 'ARK_BASE_URL') or 'https://ark.cn-beijing.volces.com/api/v3',
                          'VIDEO_API_KEY': video_setting(settings, 'ARK_API_KEY')})

    async def submit(self, job: VideoJob, references: tuple[Path, ...]) -> ProviderVideo:
        match job.request:
            case VolcengineRequest() as request:
                payload = request.model_dump(mode='json', exclude_none=True, exclude={'persona', 'request_key'})
            case OpenAIVideoRequest():
                raise GenerationError('video_protocol_invalid')
            case unreachable:
                assert_never(unreachable)
        payload.update(model=job.runtime_model, safety_identifier='easel:' + hashlib.sha256(job.persona.encode()).hexdigest()[:48])
        async with self.client() as client:
            response = await client.post(self.base + '/contents/generations/tasks', json=payload)
        response.raise_for_status()
        return self.receipt(response)

    def receipt(self, response: httpx.Response) -> ProviderVideo:
        result = VolcengineResult.model_validate_json(response.content)
        error = None
        if result.error is not None:
            cleaned = result.error.model_copy(update={
                'code': result.error.code.replace(self.key, '[redacted]'),
                'message': result.error.message.replace(self.key, '[redacted]')})
            result = result.model_copy(update={'error': cleaned})
            error = ProviderVideoError(code=cleaned.code, message=cleaned.message)
        match result.status:
            case 'queued':
                status = 'queued'
            case 'running':
                status = 'in_progress'
            case 'succeeded':
                status = 'completed'
            case 'cancelled' | 'failed' | 'expired':
                status = 'failed'
            case unreachable:
                assert_never(unreachable)
        return ProviderVideo(id=result.id, status=status, model=result.model, created_at=result.created_at,
                             video_url=result.content.video_url if result.content else '', error=error, volcengine=result)

    async def status(self, identifier: str) -> ProviderVideo:
        async with self.client() as client:
            response = await client.get(self.base + '/contents/generations/tasks/' + identifier)
        response.raise_for_status()
        result = self.receipt(response)
        if result.id != identifier:
            raise GenerationError('video_provider_identity_mismatch', 502)
        if result.status == 'completed' and not result.video_url:
            raise GenerationError('video_result_empty', 502)
        return result
