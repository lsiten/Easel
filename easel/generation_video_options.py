"""Persona-owned video capabilities and deterministic runtime selection."""
from pydantic import AliasChoices, ConfigDict, Field, JsonValue

from easel.generation_models import GenerationError, GenerationModel
from easel.generation_video_request import VideoChannel, VideoGenerationRequest


class RuntimeVideoModel(GenerationModel):
    model: str
    mode: str = ''
    resolution: str = ''


class VideoDefaults(GenerationModel):
    model_config = ConfigDict(frozen=True, extra='ignore')
    resolution: str = ''
    aspect_ratio: str = Field(default='', validation_alias=AliasChoices('aspect_ratio', 'aspectRatio'), serialization_alias='aspectRatio')
    duration: float | None = None


class VideoCapabilities(GenerationModel):
    model_config = ConfigDict(frozen=True, extra='ignore')
    channel: VideoChannel = 'openai'
    runtime_model: str = Field(default='', validation_alias=AliasChoices('runtime_model', 'runtimeModel'), serialization_alias='runtimeModel')
    runtime_models: tuple[RuntimeVideoModel, ...] = Field(default=(), validation_alias=AliasChoices('runtime_models', 'runtimeModels'), serialization_alias='runtimeModels')
    description: str = ''
    summary: str = ''
    logo: str = ''
    tags: tuple[JsonValue, ...] = ()
    main_tag: str = Field(default='', validation_alias=AliasChoices('main_tag', 'mainTag'), serialization_alias='mainTag')
    input_constraints: dict[str, JsonValue] = Field(default_factory=dict, validation_alias=AliasChoices('input_constraints', 'inputConstraints'), serialization_alias='inputConstraints')
    modes: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    ratios: tuple[str, ...] = Field(default=(), validation_alias=AliasChoices('ratios', 'aspectRatios'), serialization_alias='aspectRatios')
    durations: tuple[float, ...] = ()
    max_input_images: int | None = Field(default=None, ge=0, validation_alias=AliasChoices('max_input_images', 'maxInputImages'), serialization_alias='maxInputImages')
    defaults: VideoDefaults = Field(default_factory=VideoDefaults)

    def resolve(self, request: VideoGenerationRequest) -> tuple[str, VideoGenerationRequest]:
        images = request.primary_images() + request.images
        mode = request.mode or ('video2video' if request.video_url or request.videos else
                               'multi-image2video' if len(images) > 1 else 'image2video' if images else 'text2video')
        resolution = request.resolution or self.defaults.resolution
        ratio = request.ratio or self.defaults.aspect_ratio
        duration = request.duration if request.duration is not None else self.defaults.duration
        for value, allowed in ((mode, self.modes), (resolution, self.resolutions), (ratio, self.ratios), (duration, self.durations)):
            if value and allowed and value not in allowed:
                raise GenerationError('video_model_option_invalid')
        if self.max_input_images is not None and len(images) > self.max_input_images:
            raise GenerationError('video_image_count_invalid')
        matches = sorted((entry for entry in self.runtime_models
                          if (not entry.mode or entry.mode == mode) and (not entry.resolution or entry.resolution == resolution)),
                         key=lambda entry: bool(entry.mode) + bool(entry.resolution), reverse=True)
        if self.runtime_models and not matches:
            raise GenerationError('video_runtime_model_unavailable')
        model = matches[0].model if matches else self.runtime_model or request.model
        return model, request.model_copy(update={'mode': mode, 'resolution': resolution, 'ratio': ratio, 'duration': duration})
