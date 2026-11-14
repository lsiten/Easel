"""Shared video generation request preserving the upstream media fields."""
from collections.abc import Mapping
from typing import Literal

from pydantic import AliasChoices, Field, JsonValue, model_validator
from typing_extensions import assert_never

from easel.generation_models import GenerationError, GenerationModel
from easel.generation_volcengine_models import VideoTool


VideoChannel = Literal['openai', 'volcengine', 'grok', 'dashscope', 'relay']


class VideoReferenceInput(GenerationModel):
    value: str
    kind: Literal['image', 'video', 'audio']


class VideoGenerationRequest(GenerationModel):
    persona: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=4000)
    group_id: str = Field(default='', validation_alias=AliasChoices('group_id', 'groupId'), serialization_alias='groupId')
    image: str | tuple[str, ...] = ()
    image_tail: str = ''
    video_url: str = ''
    images: tuple[str, ...] = ()
    videos: tuple[str, ...] = ()
    audios: tuple[str, ...] = ()
    mode: str = ''
    size: str = ''
    resolution: str = ''
    ratio: str = ''
    duration: float | None = Field(default=None, allow_inf_nan=False)
    seed: int | None = None
    watermark: bool | None = None
    tools: tuple[VideoTool, ...] = ()
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_prompt(self) -> 'VideoGenerationRequest':
        if not self.prompt.strip() or '\0' in self.prompt:
            raise GenerationError('video_prompt_invalid')
        return self

    def primary_images(self) -> tuple[str, ...]:
        match self.image:
            case str() as image:
                return (image,) if image else ()
            case tuple() as images:
                return images
            case unreachable:
                assert_never(unreachable)

    def references(self) -> tuple[VideoReferenceInput, ...]:
        match self.image:
            case str() as image:
                primary = (image,) if image else ()
            case tuple() as images:
                primary = images
            case unreachable:
                assert_never(unreachable)
        visual = (*primary, *self.images, *((self.image_tail,) if self.image_tail else ()))
        videos = (*((self.video_url,) if self.video_url else ()), *self.videos)
        return (*(VideoReferenceInput(value=value, kind='image') for value in visual),
                *(VideoReferenceInput(value=value, kind='video') for value in videos),
                *(VideoReferenceInput(value=value, kind='audio') for value in self.audios))

    def replace_references(self, values: Mapping[str, str]) -> 'VideoGenerationRequest':
        match self.image:
            case str() as image:
                primary: str | tuple[str, ...] = values.get(image, image)
            case tuple() as images:
                primary = tuple(values.get(image, image) for image in images)
            case unreachable:
                assert_never(unreachable)
        return self.model_copy(update={'image': primary, 'images': tuple(values.get(value, value) for value in self.images),
            'image_tail': values.get(self.image_tail, self.image_tail), 'video_url': values.get(self.video_url, self.video_url),
            'videos': tuple(values.get(value, value) for value in self.videos),
            'audios': tuple(values.get(value, value) for value in self.audios)})
