"""Expose configured image models and reuse persona AI model preferences."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, ConfigDict, Field, JsonValue
from typing_extensions import assert_never

from easel.generation_models import GenerationError, GenerationModel, ImageRequest
from easel.local_user import LocalUsers
from easel.local_user_models import AIPreference
from skills.shared.scripts.image_provider_config import ENV_ALIASES
from skills.shared.scripts.model_registry import configured_providers


class ImageModelInfo(GenerationModel):
    name: str
    channel: str = 'openai'
    summary: str = ''
    logo: str = ''
    tags: tuple[JsonValue, ...] = ()
    mainTag: str = ''
    supportedAspectRatios: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    inputModalities: tuple[str, ...] = ()
    outputModalities: tuple[str, ...] = ()
    queuePriority: int = 1
    description: str = ''
    sizes: tuple[str, ...] = ()
    qualities: tuple[str, ...] = ()
    styles: tuple[str, ...] = ()
    max_input_images: int | None = Field(default=None, ge=1, le=16, serialization_alias='maxInputImages')
    configured: bool


class ImageModelOptions(GenerationModel):
    model_config = ConfigDict(frozen=True, extra='ignore')
    channel: str = ''
    summary: str = ''
    logo: str = ''
    tags: tuple[JsonValue, ...] = ()
    mainTag: str = ''
    queuePriority: int = Field(default=1, ge=1, le=2097152)
    supportedAspectRatios: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    inputModalities: tuple[str, ...] = ()
    outputModalities: tuple[str, ...] = ()
    description: str = Field(default='', max_length=4000)
    runtime_model: str = Field(default='', max_length=200,
                               validation_alias=AliasChoices('runtime_model', 'runtimeModel'))
    sizes: tuple[str, ...] = ()
    qualities: tuple[str, ...] = ()
    styles: tuple[str, ...] = ()
    max_input_images: int | None = Field(default=None, ge=1, le=16,
                                 validation_alias=AliasChoices('max_input_images', 'maxInputImages'))


def preference(outputs: Path, persona: str, operation: Literal['generate', 'edit']) -> AIPreference | None:
    ai = LocalUsers(outputs).read(persona).ai
    match operation:
        case 'edit':
            return ai.edit
        case 'generate':
            return ai.image
        case unreachable:
            assert_never(unreachable)


def image_models(outputs: Path, persona: str, settings: Mapping[str, str],
                 operation: Literal['generate', 'edit']) -> tuple[ImageModelInfo, ...]:
    ready = bool(configured_providers('image', dict(settings)))
    default = next((settings.get(key, '').strip() for key in ('IMG_MODEL', *ENV_ALIASES['IMG_MODEL'])
                    if settings.get(key, '').strip()), '')
    models = {default: ImageModelInfo(name=default, channel=settings.get('IMG_PROVIDER', 'openai'),
        configured=bool(settings.get('GEMINI_API_KEY')) if settings.get('IMG_PROVIDER') == 'gemini' else ready)} if default else {}
    from easel.generation_app_config import effective_config
    from easel.generation_gemini import native_ready
    definitions = effective_config(outputs).models.image
    for item in definitions.edit if operation == 'edit' else definitions.generation:
        models[item.name] = ImageModelInfo.model_validate({**item.model_dump(exclude={'runtimeModel', 'retry', 'maxInputImages'}),
            'max_input_images': item.maxInputImages, 'configured': native_ready(settings) if item.channel == 'gemini' else ready})
    selected = preference(outputs, persona, operation)
    if selected is not None:
        options = ImageModelOptions.model_validate(selected.options)
        models[selected.default_model] = ImageModelInfo(name=selected.default_model,
            description=options.description, sizes=options.sizes, qualities=options.qualities,
            summary=options.summary, logo=options.logo, tags=options.tags, mainTag=options.mainTag, queuePriority=options.queuePriority,
            styles=options.styles, max_input_images=options.max_input_images,
            supportedAspectRatios=options.supportedAspectRatios, resolutions=options.resolutions,
            inputModalities=options.inputModalities, outputModalities=options.outputModalities,
            channel=options.channel or settings.get('IMG_PROVIDER', 'openai'),
            configured=bool(settings.get('GEMINI_API_KEY')) if options.channel == 'gemini' else ready)
    return tuple(models.values())


def runtime_image_model(outputs: Path, request: ImageRequest) -> str:
    from easel.generation_app_config import effective_config
    definitions = effective_config(outputs).models.image
    definition = next((item for item in (definitions.edit if request.operation == 'edit' else definitions.generation) if item.name == request.model), None)
    selected = preference(outputs, request.persona, request.operation)
    if definition is not None and (selected is None or selected.default_model != request.model):
        options = ImageModelOptions.model_validate(definition.model_dump())
    elif selected is not None and selected.default_model == request.model:
        options = ImageModelOptions.model_validate(selected.options)
    else:
        return request.model
    for value, allowed in ((request.size, options.sizes), (request.quality, options.qualities),
                           (request.style, options.styles), (request.aspectRatio, options.supportedAspectRatios),
                           (request.imageSize, options.resolutions)):
        if value and allowed and value not in allowed:
            raise GenerationError('generation_model_option_invalid')
    if options.max_input_images is not None and len(request.images) > options.max_input_images:
        raise GenerationError('generation_model_input_limit')
    return options.runtime_model or request.model


def image_channel(outputs: Path, request: ImageRequest, settings: Mapping[str, str]) -> str:
    if request.channel:
        return request.channel
    available = image_models(outputs, request.persona, settings, request.operation)
    selected = next((item for item in available if item.name == request.model), None)
    channel = selected.channel if selected else settings.get('IMG_PROVIDER', '')
    return channel if channel in ('gemini', 'agnes') else ''
