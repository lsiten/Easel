"""Chat catalog backed by existing local model settings."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from easel.direct_api import api_config, DirectAPIError
from easel.local_user import LocalUsers
from easel.generation_models import GenerationError
from easel.generation_chat_models import ChatModel


@dataclass(frozen=True, slots=True)
class ChatEndpoint:
    url: str
    key: str
    model: str
    channel: str


CHAT_PROVIDER_ORDER = {'openai': 0, 'anthropic': 1}


def _chat_provider_ready(settings: Mapping[str, str], channel: str) -> bool:
    if channel == 'anthropic':
        return bool((settings.get('ANTHROPIC_API_KEY') or settings.get('ANTHROPIC_AUTH_TOKEN') or '').strip())
    if settings.get('EASEL_DIRECT_API_BASE_URL') and settings.get('EASEL_DIRECT_API_MODEL'):
        try:
            api_config(settings)
            return True
        except DirectAPIError:
            return False
    openai_ready = bool(settings.get('OPENAI_API_KEY', '').strip())
    return openai_ready


def chat_models(outputs: Path, persona: str, settings: Mapping[str, str]) -> tuple[ChatModel, ...]:
    from easel.generation_app_config import effective_config
    models: list[ChatModel] = []
    for name, channel in ((settings.get('EASEL_DIRECT_API_MODEL', ''), 'openai'),
                          (settings.get('OPENAI_MODEL', ''), 'openai'),
                          (settings.get('CLAUDE_MODEL', ''), 'anthropic'),
                          (settings.get('ANTHROPIC_MODEL', ''), 'anthropic')):
        if name:
            models.append(ChatModel(name=name, channel=channel,
                                    configured=_chat_provider_ready(settings, channel)))
    models.extend(item.model_copy(update={
        'configured': _chat_provider_ready(settings, item.channel),
    }) for item in effective_config(outputs).models.chat)
    selected = LocalUsers(outputs).read(persona).ai.agent
    if selected:
        channel = str(selected.options.get('channel') or 'openai')
        prior = next((item for item in models if
                      item.name == selected.default_model and item.channel == channel), None)
        models = [item for item in models if not (
            item.name == selected.default_model and item.channel == channel)]
        models.append(ChatModel.model_validate({
            **(prior.model_dump() if prior else {}),
            **selected.options,
            'name': selected.default_model,
            'channel': channel,
            'configured': _chat_provider_ready(settings, channel),
        }))
    unique: list[ChatModel] = []
    seen: set[tuple[str, str]] = set()
    for item in models:
        key = (item.channel, item.name)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    unique.sort(key=lambda item: (not item.configured, CHAT_PROVIDER_ORDER.get(item.channel, 99)))
    return tuple(unique)


def default_chat_model(outputs: Path, persona: str, settings: Mapping[str, str]) -> ChatModel | None:
    """Resolve a default from the chat channel without a second planner config."""
    available = chat_models(outputs, persona, settings)
    if not available:
        return None
    selected = LocalUsers(outputs).read(persona).ai.agent
    preferences = (
        selected.default_model if selected else '',
        settings.get('EASEL_DIRECT_API_MODEL', '').strip(),
        settings.get('OPENAI_MODEL', '').strip(),
        settings.get('CLAUDE_MODEL', '').strip(),
        settings.get('ANTHROPIC_MODEL', '').strip(),
    )
    for name in preferences:
        if name:
            match = next((item for item in available if item.name == name and item.configured), None)
            if match is not None:
                return match
    return next((item for item in available if item.configured), available[0])


def endpoint(settings: Mapping[str, str], model: ChatModel, claude: bool) -> ChatEndpoint:
    config = dict(settings)
    if claude:
        config.update(EASEL_DIRECT_API_BASE_URL=settings.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com/v1'),
                      EASEL_DIRECT_API_KEY=settings.get('ANTHROPIC_API_KEY', settings.get('ANTHROPIC_AUTH_TOKEN', '')))
    elif model.channel == 'openai' and not settings.get('EASEL_DIRECT_API_BASE_URL'):
        config.update(
            EASEL_DIRECT_API_BASE_URL=settings.get('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
            EASEL_DIRECT_API_KEY=settings.get('OPENAI_API_KEY', ''),
        )
    config['EASEL_DIRECT_API_MODEL'] = model.runtime_model or model.name
    try:
        base, runtime, key = api_config(config)
    except DirectAPIError:
        raise GenerationError('chat_provider_configuration_invalid', 409) from None
    if claude and not base.endswith('/v1'):
        base += '/v1'
    return ChatEndpoint(base + ('/messages' if claude else '/chat/completions'), key, runtime, model.channel)
