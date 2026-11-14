"""Video model catalog from configured providers and persona preferences."""
from collections.abc import Mapping
from pathlib import Path
from easel.generation_video_options import VideoCapabilities
from easel.generation_video_models import VideoModelOptions
from easel.generation_video_provider import video_setting
from easel.local_user import LocalUsers
from skills.shared.scripts.model_registry import provider_catalog, provider_for_model_channel


class VideoModelInfo(VideoCapabilities):
    name: str
    configured: bool


def runtime_video_model(outputs: Path, persona: str, model: str) -> str:
    from easel.generation_app_config import effective_config
    definitions = effective_config(outputs).models.video.generation
    selected = LocalUsers(outputs).read(persona).ai.video
    if selected is None or selected.default_model != model:
        return next((item.runtime_model or model for item in definitions if item.name == model), model)
    if not selected.options:
        return next((item.runtime_model or model for item in definitions if item.name == model), model)
    options = VideoModelOptions.model_validate(selected.options)
    return options.runtime_model or model


def video_models(outputs: Path, persona: str, settings: Mapping[str, str]) -> tuple[VideoModelInfo, ...]:
    providers = provider_catalog('video', dict(settings), video_setting(settings, 'VIDEO_PROVIDER'))
    provider_rank = {str(row['id']): int(row['order']) for row in providers}
    readiness = {
        channel: next(
            (bool(row['configured']) for row in providers
             if row['id'] == provider_for_model_channel('video', channel)),
            False,
        )
        for channel in ('openai', 'volcengine', 'grok', 'dashscope', 'relay')
    }
    values: list[VideoModelInfo] = []
    for channel, model in (
        ('openai', video_setting(settings, 'VIDEO_MODEL')),
        ('volcengine', video_setting(settings, 'ARK_MODEL')),
        *((channel, video_setting(settings, prefix + '_VIDEO_MODEL'))
          for channel, prefix in (('grok', 'GROK'), ('dashscope', 'DASHSCOPE'), ('relay', 'RELAY'))),
    ):
        if model:
            values.append(VideoModelInfo(name=model, channel=channel, configured=readiness[channel]))
    from easel.generation_app_config import effective_config
    for item in effective_config(outputs).models.video.generation:
        values.append(VideoModelInfo.model_validate({
            **item.model_dump(exclude={'queuePriority', 'retry'}),
            'configured': readiness.get(item.channel, False),
        }))
    selected = LocalUsers(outputs).read(persona).ai.video
    if selected is not None:
        if not selected.options and any(value.name == selected.default_model for value in values):
            return _ordered_video_models(values, provider_rank)
        options = VideoCapabilities.model_validate(selected.options)
        values = [value for value in values if not (
            value.name == selected.default_model and value.channel == options.channel)]
        values.append(VideoModelInfo(
            name=selected.default_model,
            channel=options.channel,
            configured=readiness.get(options.channel, False),
            **options.model_dump(exclude={'channel'}),
        ))
    return _ordered_video_models(values, provider_rank)


def _ordered_video_models(models: list[VideoModelInfo],
                          provider_rank: Mapping[str, int]) -> tuple[VideoModelInfo, ...]:
    unique: list[VideoModelInfo] = []
    seen: set[tuple[str, str]] = set()
    for item in models:
        key = (item.channel, item.name)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    unique.sort(key=lambda item: (
        not item.configured,
        provider_rank.get(provider_for_model_channel('video', item.channel), len(provider_rank) + 1),
    ))
    return tuple(unique)
