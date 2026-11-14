"""Source Sora character contracts without provider-global ID access."""
from typing import Literal

from pydantic import ConfigDict, Field, HttpUrl, model_validator

from easel.generation_models import GenerationError, GenerationModel


class CharacterRequest(GenerationModel):
    persona: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=4000)
    videoUrl: HttpUrl | None = None
    taskId: str = Field(default='', max_length=256)
    timestamps: str = Field(pattern=r'^\d+,\d+$', max_length=30)

    @model_validator(mode='after')
    def source_required(self) -> 'CharacterRequest':
        if not self.taskId and not self.videoUrl:
            raise GenerationError('character_video_source_required')
        start, end = (int(value) for value in self.timestamps.split(','))
        if not 0 < end - start <= 3 or not self.prompt.strip() or '\0' in self.prompt:
            raise GenerationError('character_clip_invalid')
        return self


class CharacterError(GenerationModel):
    code: str | int
    message: str


class CharacterReceipt(GenerationModel):
    model_config = ConfigDict(frozen=True, extra='ignore')
    id: str = Field(min_length=1, max_length=256, pattern=r'^[A-Za-z0-9_-]+$')
    object: Literal['character'] = 'character'
    model: Literal['sora-2-character'] = 'sora-2-character'
    status: Literal['queued', 'processing', 'completed', 'failed']
    username: str = ''
    avatar_url: str = ''
    video_url: str = ''
    created_at: float = Field(default=0, ge=0, allow_inf_nan=False)
    completed_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    error: CharacterError | None = None


class CharacterJob(GenerationModel):
    id: str
    persona: str
    request: CharacterRequest
    state: Literal['running', 'succeeded', 'failed', 'unknown'] = 'running'
    provider_id: str = ''
    response: CharacterReceipt | None = None
    created_at: float
    updated_at: float
    error: str = ''
