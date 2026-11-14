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
from skills.shared.scripts.image_provider_config import ENV_ALIASES, detect_provider
from skills.shared.scripts.model_registry import provider_catalog, provider_for_model_channel


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


def image_provider_rows(settings: Mapping[str, str]) -> list[dict[str, object]]:
    preferred = settings.get('IMG_PROVIDER', '').strip()
    return provider_catalog('image', dict(settings), preferred)


def image_provider_ready(settings: Mapping[str, str], channel: str) -> bool:
    provider_id = provider_for_model_channel('image', channel or 'openai')
    return next((bool(row['configured']) for row in image_provider_rows(settings)
                 if row['id'] == provider_id), False)


def _default_image(settings: Mapping[str, str]) -> tuple[str, str]:
    model = next((settings.get(key, '').strip() for key in ('IMG_MODEL', *ENV_ALIASES['IMG_MODEL'])
                  if settings.get(key, '').strip()), '')
    if model:
        explicit = settings.get('IMG_PROVIDER', '').strip()
        if explicit in {'openai', 'agnes', 'gemini'}:
            return model, explicit
        base = next((settings.get(key, '').strip() for key in ('IMG_BASE_URL', *ENV_ALIASES['IMG_BASE_URL'])
                     if settings.get(key, '').strip()), '')
        return model, detect_provider(base)
    native_model = settings.get('GEMINI_IMAGE_MODEL', '').strip()
    if native_model and image_provider_ready(settings, 'gemini'):
        return native_model, 'gemini'
    return '', 'openai'


def image_models(outputs: Path, persona: str, settings: Mapping[str, str],
                 operation: Literal['generate', 'edit']) -> tuple[ImageModelInfo, ...]:
    providers = image_provider_rows(settings)
    provider_rank = {str(row['id']): int(row['order']) for row in providers}
    default, default_channel = _default_image(settings)
    models: list[ImageModelInfo] = []
    if default:
        models.append(ImageModelInfo(name=default, channel=default_channel,
            configured=image_provider_ready(settings, default_channel)))
    from easel.generation_app_config import effective_config
    definitions = effective_config(outputs).models.image
    for item in definitions.edit if operation == 'edit' else definitions.generation:
        models.append(ImageModelInfo.model_validate({
            **item.model_dump(exclude={'runtimeModel', 'retry', 'maxInputImages'}),
            'max_input_images': item.maxInputImages,
            'configured': image_provider_ready(settings, item.channel),
        }))
    selected = preference(outputs, persona, operation)
    if selected is not None:
        if not selected.options and any(item.name == selected.default_model for item in models):
            return _ordered_image_models(models, provider_rank)
        options = ImageModelOptions.model_validate(selected.options)
        channel = options.channel or default_channel or 'openai'
        models = [item for item in models if not (
            item.name == selected.default_model and item.channel == channel)]
        models.append(ImageModelInfo(name=selected.default_model,
            description=options.description, sizes=options.sizes, qualities=options.qualities,
            summary=options.summary, logo=options.logo, tags=options.tags, mainTag=options.mainTag, queuePriority=options.queuePriority,
            styles=options.styles, max_input_images=options.max_input_images,
            supportedAspectRatios=options.supportedAspectRatios, resolutions=options.resolutions,
            inputModalities=options.inputModalities, outputModalities=options.outputModalities,
            channel=channel, configured=image_provider_ready(settings, channel)))
    return _ordered_image_models(models, provider_rank)


def _ordered_image_models(models: list[ImageModelInfo],
                          provider_rank: Mapping[str, int]) -> tuple[ImageModelInfo, ...]:
    unique: list[ImageModelInfo] = []
    seen: set[tuple[str, str]] = set()
    for item in models:
        key = (item.channel, item.name)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    unique.sort(key=lambda item: (
        not item.configured,
        provider_rank.get(provider_for_model_channel('image', item.channel), len(provider_rank) + 1),
    ))
    return tuple(unique)


def runtime_image_model(outputs: Path, request: ImageRequest, channel: str = '') -> str:
    from easel.generation_app_config import effective_config
    definitions = effective_config(outputs).models.image
    selected = preference(outputs, request.persona, request.operation)
    preferred_channel = channel or request.channel
    definition = next((item for item in (
        definitions.edit if request.operation == 'edit' else definitions.generation)
        if item.name == request.model and (not preferred_channel or item.channel == preferred_channel)), None)
    if definition is not None and (selected is None or selected.default_model != request.model):
        options = ImageModelOptions.model_validate(definition.model_dump())
    elif selected is not None and selected.default_model == request.model:
        if not selected.options:
            definition = next((item for item in (
                definitions.edit if request.operation == 'edit' else definitions.generation)
                if item.name == request.model and (not preferred_channel or item.channel == preferred_channel)), None)
            return definition.runtime_model if definition and definition.runtime_model else request.model
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
    matching = [item for item in available if item.name == request.model]
    selected = next((item for item in matching if item.configured), matching[0] if matching else None)
    channel = selected.channel if selected else _default_image(settings)[1]
    return channel if channel in ('gemini', 'agnes') else ''
