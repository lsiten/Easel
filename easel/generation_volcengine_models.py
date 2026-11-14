"""Pinned Volcengine native content and response contracts."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from easel.generation_models import GenerationError, GenerationModel


class VideoURL(GenerationModel):
    url: str = Field(min_length=1, max_length=70 * 1024**2)


class VideoText(GenerationModel):
    type: Literal['text'] = 'text'
    text: str


class VideoImage(GenerationModel):
    type: Literal['image_url'] = 'image_url'
    image_url: VideoURL
    role: Literal['first_frame', 'last_frame', 'reference_image'] | None = None


class VideoReference(GenerationModel):
    type: Literal['video_url'] = 'video_url'
    video_url: VideoURL
    role: Literal['reference_video']


class VideoAudio(GenerationModel):
    type: Literal['audio_url'] = 'audio_url'
    audio_url: VideoURL
    role: Literal['reference_audio']


VideoContent = Annotated[VideoText | VideoImage | VideoReference | VideoAudio, Field(discriminator='type')]


class VideoTool(GenerationModel):
    type: Literal['web_search']


class VolcengineRequest(GenerationModel):
    persona: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    content: tuple[VideoContent, ...] = Field(min_length=1)
    return_last_frame: bool | None = None
    tools: tuple[VideoTool, ...] | None = None
    resolution: str | None = None
    ratio: str | None = None
    duration: int | None = None
    seed: int | None = None
    watermark: bool | None = None

    @model_validator(mode='after')
    def valid_scene(self) -> VolcengineRequest:
        images = tuple(item for item in self.content if isinstance(item, VideoImage))
        first = sum(item.role in (None, 'first_frame') for item in images)
        last = sum(item.role == 'last_frame' for item in images)
        reference_images = sum(item.role == 'reference_image' for item in images)
        videos = sum(isinstance(item, VideoReference) for item in self.content)
        audios = sum(isinstance(item, VideoAudio) for item in self.content)
        has_prompt = any(isinstance(item, VideoText) and item.text.strip() for item in self.content)
        if not has_prompt or first > 1 or last > 1 or last and not first:
            raise GenerationError('video_frame_scene_invalid')
        if (first or last) and (reference_images or videos or audios):
            raise GenerationError('video_reference_scene_conflict')
        if audios and not (reference_images or videos):
            raise GenerationError('video_audio_reference_requires_visual')
        return self


class VolcengineMetadata(GenerationModel):
    model_config = ConfigDict(frozen=True, extra='ignore')


class VolcengineError(VolcengineMetadata):
    code: str
    message: str


class VolcengineOutput(VolcengineMetadata):
    video_url: str
    last_frame_url: str | None = None


class VolcengineToolUsage(VolcengineMetadata):
    web_search: int | None = None


class VolcengineUsage(VolcengineMetadata):
    completion_tokens: int
    total_tokens: int
    tool_usage: VolcengineToolUsage | None = None


class VolcengineResult(GenerationModel):
    model_config = ConfigDict(frozen=True, extra='ignore')
    id: str = Field(min_length=1, max_length=256, pattern=r'^[A-Za-z0-9_-]+$')
    model: str = ''
    status: Literal['queued', 'running', 'cancelled', 'succeeded', 'failed', 'expired'] = 'queued'
    created_at: float | None = None
    updated_at: float | None = None
    content: VolcengineOutput | None = None
    error: VolcengineError | None = None
    seed: int | None = None
    resolution: str | None = None
    ratio: str | None = None
    duration: float | None = None
    framespersecond: float | None = None
    usage: VolcengineUsage | None = None
