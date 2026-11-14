"""Source-compatible Grok, DashScope and Relay video HTTP transports."""
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import httpx
from pydantic import ConfigDict, JsonValue
from typing_extensions import assert_never

from easel.generation_models import GenerationError, GenerationModel
from easel.generation_video_generic_payload import json_payload
from easel.generation_video_models import ProviderVideo, ProviderVideoError, VideoJob
from easel.generation_video_provider import NativeVideoProvider, video_setting
from easel.generation_video_receipts import DashscopeVideoReceipt, GrokVideoReceipt, RelayVideoReceipt, VideoProviderError


class RelayEnvelope(GenerationModel):
    model_config = ConfigDict(frozen=True, extra='ignore')
    code: int | None = None
    message: str = ''
    data: RelayVideoReceipt | None = None


class GenericVideoProvider(NativeVideoProvider):
    def __init__(self, settings: Mapping[str, str], channel: Literal['grok', 'dashscope', 'relay']) -> None:
        self.channel = channel
        defaults = {'grok': 'https://api.x.ai', 'dashscope': 'https://dashscope.aliyuncs.com', 'relay': ''}
        base = settings.get(channel.upper() + '_BASE_URL', '').strip() or defaults[channel]
        if channel == 'dashscope' and base.rstrip('/').endswith('/api/v1'):
            base = base.rstrip('/')[:-7]
        super().__init__({'VIDEO_BASE_URL': base, 'VIDEO_API_KEY': video_setting(settings, channel.upper() + '_API_KEY')})

    def client(self) -> httpx.AsyncClient:
        headers = {'x-api-key': self.key} if self.channel == 'relay' else {'Authorization': 'Bearer ' + self.key}
        if self.channel == 'dashscope':
            headers['X-DashScope-Async'] = 'enable'
        return httpx.AsyncClient(trust_env=False, follow_redirects=False, headers=headers,
            timeout=httpx.Timeout(connect=10, read=60, write=60, pool=10),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))

    async def submit(self, job: VideoJob, references: tuple[Path, ...]) -> ProviderVideo:
        request = job.effective_request
        if request is None:
            raise GenerationError('video_protocol_invalid')
        match self.channel:
            case 'grok':
                endpoint = '/v1/videos/edits' if request.video_url else '/v1/videos/generations'
            case 'dashscope':
                endpoint = '/api/v1/services/aigc/video-generation/video-synthesis'
            case 'relay':
                endpoint = '/api/ai/video/generations'
            case unreachable:
                assert_never(unreachable)
        async with self.client() as client:
            response = await client.post(self.base + endpoint, json=json_payload(request, self.channel, job.runtime_model))
        response.raise_for_status()
        return self.parse_receipt(response)

    async def status(self, identifier: str) -> ProviderVideo:
        paths = {'grok': '/v1/videos/', 'dashscope': '/api/v1/tasks/', 'relay': '/api/ai/video/generations/'}
        async with self.client() as client:
            response = await client.get(self.base + paths[self.channel] + identifier)
        response.raise_for_status()
        result = self.parse_receipt(response, identifier)
        if result.id != identifier:
            raise GenerationError('video_provider_identity_mismatch', 502)
        return result

    def parse_receipt(self, response: httpx.Response, identifier: str = '') -> ProviderVideo:
        match self.channel:
            case 'grok':
                receipt = GrokVideoReceipt.model_validate_json(response.content)
                native_id = receipt.request_id or identifier
                url = receipt.video.url if receipt.video else ''
                failed = receipt.status in {'failed', 'expired'} or receipt.status == 'done' and not url
                status = 'failed' if failed else 'completed' if url else 'queued'
                error = ProviderVideoError(code=receipt.error.code, message=receipt.error.message) if receipt.error else None
                result = ProviderVideo(id=native_id, status=status, video_url=url, error=error, grok=receipt)
            case 'dashscope':
                receipt = DashscopeVideoReceipt.model_validate_json(response.content)
                if receipt.output is None:
                    raise GenerationError('video_provider_receipt_invalid', 502)
                output = receipt.output
                status = {'PENDING': 'queued', 'RUNNING': 'in_progress', 'SUCCEEDED': 'completed',
                          'FAILED': 'failed', 'CANCELED': 'failed', 'UNKNOWN': 'failed'}[output.task_status]
                if status == 'completed' and not output.video_url:
                    status = 'failed'
                error = ProviderVideoError(code=output.code, message=output.message) if output.code or output.message else None
                result = ProviderVideo(id=output.task_id, status=status, video_url=output.video_url, error=error, dashscope=receipt)
            case 'relay':
                envelope = RelayEnvelope.model_validate_json(response.content)
                if envelope.code is not None and envelope.code != 0:
                    raise GenerationError('video_relay_rejected', 502)
                receipt = envelope.data or RelayVideoReceipt.model_validate_json(response.content)
                status = 'completed' if receipt.status.lower() in {'success', 'succeeded', 'completed', 'succeed'} else (
                    'failed' if receipt.status.lower() in {'failed', 'error', 'failure'} else 'in_progress')
                if status == 'completed' and not receipt.videoUrl:
                    status = 'failed'
                match receipt.error:
                    case str() as message:
                        error = ProviderVideoError(code='relay_error', message=message)
                    case None:
                        error = None
                    case VideoProviderError() as provider_error:
                        error = ProviderVideoError(code=provider_error.code, message=provider_error.message)
                    case unreachable:
                        assert_never(unreachable)
                result = ProviderVideo(id=receipt.id or identifier, status=status, video_url=receipt.videoUrl, error=error, relay=receipt)
            case unreachable:
                assert_never(unreachable)
        return ProviderVideo.model_validate(redact(result.model_dump(mode='json'), self.key))


def redact(value: JsonValue, key: str) -> JsonValue:
    """Keep provider error metadata while removing credentials from every string leaf."""
    match value:
        case str() as text:
            return text.replace(key, '[redacted]')
        case list() as items:
            return [redact(item, key) for item in items]
        case dict() as fields:
            return {name: redact(item, key) for name, item in fields.items()}
        case None | bool() | int() | float():
            return value
        case unreachable:
            assert_never(unreachable)
