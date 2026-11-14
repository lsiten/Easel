"""Native OpenAI video requests and durable provider checkpoints."""
from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, ConfigDict, Field, model_validator

from easel.generation_models import GeneratedAsset, GenerationError, GenerationModel
from easel.ownership import MediaManifestEntry
from easel.generation_volcengine_models import VolcengineRequest, VolcengineResult
from easel.generation_video_request import VideoChannel, VideoGenerationRequest
from easel.generation_video_receipts import DashscopeVideoReceipt, GrokVideoReceipt, RelayVideoReceipt


class OpenAIVideoRequest(GenerationModel):
    persona: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=4000)
    input_reference: str = Field(default='', max_length=70 * 1024**2)
    model: str = Field(default='', max_length=200)
    seconds: str = Field(default='', max_length=20)
    size: Literal['', '720x1280', '1280x720', '1024x1792', '1792x1024'] = ''

    @model_validator(mode='after')
    def valid_text(self) -> OpenAIVideoRequest:
        if not self.prompt.strip() or any('\0' in text for text in (
                self.prompt, self.model, self.seconds, self.input_reference)):
            raise GenerationError('video_request_invalid')
        return self


class VideoRemixRequest(GenerationModel):
    persona: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=4000)


class VideoModelOptions(GenerationModel):
    model_config = ConfigDict(frozen=True, extra='ignore')
    runtime_model: str = Field(default='', max_length=200, validation_alias=AliasChoices('runtime_model', 'runtimeModel'))


class ProviderVideoError(GenerationModel):
    model_config = ConfigDict(frozen=True, extra='ignore')
    code: str
    message: str


class ProviderVideo(GenerationModel):
    model_config = ConfigDict(frozen=True, extra='ignore')
    id: str = Field(min_length=1, max_length=256, pattern=r'^[A-Za-z0-9_-]+$')
    status: Literal['queued', 'in_progress', 'completed', 'failed']
    model: str = ''
    progress: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    created_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    completed_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    expires_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    seconds: str = ''
    size: str = ''
    url: str = ''
    video_url: str = ''
    prompt: str | None = None
    object: Literal['video'] = 'video'
    remixed_from_video_id: str | None = None
    error: ProviderVideoError | None = None
    volcengine: VolcengineResult | None = None
    grok: GrokVideoReceipt | None = None
    dashscope: DashscopeVideoReceipt | None = None
    relay: RelayVideoReceipt | None = None


VideoState = Literal['queued', 'submitting', 'waiting', 'succeeded', 'failed', 'unknown']
VideoRequest = OpenAIVideoRequest | VolcengineRequest | VideoGenerationRequest


class VideoTarget(GenerationModel):
    base: str
    model: str
    channel: VideoChannel = 'openai'
    source_id: str = ''
    effective_request: VideoGenerationRequest | None = None


class VideoJob(GenerationModel):
    id: str
    persona: str
    state: VideoState
    request: VideoRequest
    channel: VideoChannel = 'openai'
    base_url: str
    runtime_model: str
    source_id: str = ''
    provider_id: str = ''
    response: ProviderVideo | None = None
    inputs: tuple[MediaManifestEntry, ...] = ()
    asset: GeneratedAsset | None = None
    additional_assets: tuple[GeneratedAsset, ...] = ()
    created_at: float
    updated_at: float
    error: str = ''
    effective_request: VideoGenerationRequest | None = None
    media_id: str = ''
    media_error: str = ''


class VideoSummary(GenerationModel):
    id: str
    persona: str
    state: VideoState
    model: str
    channel: VideoChannel = 'openai'
    operation: Literal['create', 'remix']
    created_at: float
    updated_at: float


class VideoPage(GenerationModel):
    items: tuple[VideoSummary, ...]
    total: int
    page: int
    page_size: int
