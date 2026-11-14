"""Video model catalog from configured providers and persona preferences."""
from collections.abc import Mapping
from pathlib import Path
from easel.generation_video_options import VideoCapabilities
from easel.generation_video_models import VideoModelOptions
from easel.generation_video_provider import video_setting
from easel.local_user import LocalUsers


class VideoModelInfo(VideoCapabilities):
    name: str
    configured: bool


def runtime_video_model(outputs: Path, persona: str, model: str) -> str:
    from easel.generation_app_config import effective_config
    definitions = effective_config(outputs).models.video.generation
    selected = LocalUsers(outputs).read(persona).ai.video
    if selected is None or selected.default_model != model:
        return next((item.runtime_model or model for item in definitions if item.name == model), model)
    options = VideoModelOptions.model_validate(selected.options)
    return options.runtime_model or model


def video_models(outputs: Path, persona: str, settings: Mapping[str, str]) -> tuple[VideoModelInfo, ...]:
    openai_ready = bool(video_setting(settings, 'VIDEO_API_KEY') and video_setting(settings, 'VIDEO_BASE_URL'))
    ark_ready = bool(video_setting(settings, 'ARK_API_KEY'))
    values = {}
    for channel, model, ready in (('openai', video_setting(settings, 'VIDEO_MODEL'), openai_ready),
                                  ('volcengine', video_setting(settings, 'ARK_MODEL'), ark_ready),
                                  *((channel, video_setting(settings, prefix + '_VIDEO_MODEL'), bool(video_setting(settings, prefix + '_API_KEY')))
                                    for channel, prefix in (('grok', 'GROK'), ('dashscope', 'DASHSCOPE'), ('relay', 'RELAY')))):
        if model:
            values[channel, model] = VideoModelInfo(name=model, channel=channel, configured=ready)
    from easel.generation_app_config import effective_config
    for item in effective_config(outputs).models.video.generation:
        ready = {'openai': openai_ready, 'volcengine': ark_ready}.get(item.channel, bool(video_setting(settings, item.channel.upper() + '_API_KEY')))
        values[item.channel, item.name] = VideoModelInfo.model_validate({**item.model_dump(exclude={'queuePriority', 'retry'}), 'configured': ready})
    selected = LocalUsers(outputs).read(persona).ai.video
    if selected is not None:
        options = VideoCapabilities.model_validate(selected.options)
        values[options.channel, selected.default_model] = VideoModelInfo(name=selected.default_model,
            channel=options.channel, configured=({'openai': openai_ready, 'volcengine': ark_ready}.get(options.channel, bool(video_setting(settings, options.channel.upper() + '_API_KEY')))),
            **options.model_dump(exclude={'channel'}))
    return tuple(values.values())
