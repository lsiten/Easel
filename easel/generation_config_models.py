"""Typed application configuration for Easel's local AI runtime."""
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field, SecretStr, model_validator, ConfigDict
from easel.generation_models import GenerationModel, GenerationError
from easel.generation_chat_models import ChatModel
from easel.generation_video_options import VideoCapabilities


class ProviderConfig(GenerationModel):
    baseUrl: str = ''
    apiKey: SecretStr = SecretStr('')

    @model_validator(mode='after')
    def safe_endpoint(self):
        if self.baseUrl:
            try:
                url = urlsplit(self.baseUrl)
                url.port
            except ValueError as error:
                raise GenerationError('config_url_invalid') from error
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment or any(ord(c) < 32 for c in self.baseUrl):
                raise GenerationError('config_url_invalid')
        return self


class ImageDefinition(GenerationModel):
    name: str = Field(min_length=1, max_length=200)
    channel: Literal['openai', 'agnes', 'gemini'] = 'openai'
    description: str = ''
    summary: str = ''
    logo: str = ''
    tags: tuple[dict[str, str], ...] = ()
    mainTag: str = ''
    runtimeModel: str = ''
    retry: int = Field(default=0, ge=0, le=10)
    sizes: tuple[str, ...] = ()
    qualities: tuple[str, ...] = ()
    styles: tuple[str, ...] = ()
    supportedAspectRatios: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    inputModalities: tuple[Literal['text', 'image', 'video', 'audio'], ...] = ('text', 'image')
    outputModalities: tuple[Literal['text', 'image', 'video', 'audio'], ...] = ('image',)
    maxInputImages: int | None = Field(default=None, ge=1, le=16)
    queuePriority: int = Field(default=1, ge=1, le=2097152)


class ChatDefinition(ChatModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    runtime_model: str = ''
    inputModalities: tuple[Literal['text', 'image', 'video', 'audio'], ...] = ()
    outputModalities: tuple[Literal['text', 'image', 'video', 'audio'], ...] = ()


class VideoDefinition(VideoCapabilities):
    model_config = ConfigDict(frozen=True, extra='forbid', populate_by_name=True)
    name: str = Field(min_length=1, max_length=200)
    queuePriority: int = Field(default=1, ge=1, le=2097152)
    retry: int = Field(default=0, ge=0, le=10)


class ImageDefinitions(GenerationModel):
    generation: tuple[ImageDefinition, ...] = ()
    edit: tuple[ImageDefinition, ...] = ()


class VideoDefinitions(GenerationModel):
    generation: tuple[VideoDefinition, ...] = ()


class ModelsConfig(GenerationModel):
    chat: tuple[ChatDefinition, ...] = ()
    image: ImageDefinitions = Field(default_factory=ImageDefinitions)
    video: VideoDefinitions = Field(default_factory=VideoDefinitions)


class QueueConfig(GenerationModel):
    imageConcurrency: int = Field(default=3, ge=1, le=64)
    videoConcurrency: int = Field(default=3, ge=1, le=64)
    draftConcurrency: int = Field(default=60, ge=1, le=128)
    lowPriorityMinPriority: int = Field(default=1000, ge=1, le=2097152)
    lowPriorityConcurrency: int = Field(default=2, ge=1, le=64)


class ApplicationConfig(GenerationModel):
    plannerModel: str = ''


class AppConfig(GenerationModel):
    providers: dict[Literal['openai', 'image', 'video', 'gemini', 'anthropic', 'grok', 'relay', 'dashscope', 'volcengine'], ProviderConfig] = Field(default_factory=dict)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    application: ApplicationConfig = Field(default_factory=ApplicationConfig)


class ConfigUpdate(GenerationModel):
    config: AppConfig
    revision: str = ''
    clearSecrets: tuple[str, ...] = ()
