"""Whitelisted generic video provider receipts."""
from typing import Literal

from pydantic import ConfigDict, Field

from easel.generation_models import GenerationModel


class ProviderMetadata(GenerationModel):
    model_config = ConfigDict(frozen=True, extra='ignore')


class VideoProviderError(ProviderMetadata):
    code: str = ''
    message: str = ''


class GrokVideoOutput(ProviderMetadata):
    url: str
    duration: float | None = None
    respect_moderation: bool | None = None


class GrokVideoReceipt(ProviderMetadata):
    request_id: str | None = Field(default=None, max_length=256, pattern=r'^[A-Za-z0-9_-]+$')
    status: Literal['pending', 'done', 'failed', 'expired'] | None = None
    model: str = ''
    video: GrokVideoOutput | None = None
    error: VideoProviderError | None = None


class DashscopeVideoOutput(ProviderMetadata):
    task_id: str = Field(min_length=1, max_length=256, pattern=r'^[A-Za-z0-9_-]+$')
    task_status: Literal['PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED', 'UNKNOWN'] = 'PENDING'
    submit_time: str = ''
    scheduled_time: str = ''
    end_time: str = ''
    orig_prompt: str = ''
    video_url: str = ''
    code: str = ''
    message: str = ''


class DashscopeVideoUsage(ProviderMetadata):
    duration: float | None = None
    input_video_duration: float | None = None
    output_video_duration: float | None = None
    video_count: int | None = None
    SR: float | None = None
    ratio: str | None = None


class DashscopeVideoReceipt(ProviderMetadata):
    output: DashscopeVideoOutput | None = None
    request_id: str = ''
    code: str = ''
    message: str = ''
    usage: DashscopeVideoUsage | None = None


class RelayVideoReceipt(ProviderMetadata):
    id: str | None = Field(default=None, max_length=256, pattern=r'^[A-Za-z0-9_-]+$')
    status: str = ''
    videoUrl: str = ''
    coverUrl: str = ''
    error: str | VideoProviderError | None = None
