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


def chat_models(outputs: Path, persona: str, settings: Mapping[str, str]) -> tuple[ChatModel, ...]:
    from easel.generation_app_config import effective_config
    models = {item.name: item for item in effective_config(outputs).models.chat}
    for name, channel in ((settings.get('EASEL_DIRECT_API_MODEL', ''), 'openai'),
                          (settings.get('ANTHROPIC_MODEL', ''), 'anthropic')):
        if name:
            models[name] = ChatModel(name=name, channel=channel)
    selected = LocalUsers(outputs).read(persona).ai.agent
    if selected:
        models[selected.default_model] = ChatModel.model_validate({**selected.options, 'name': selected.default_model})
    return tuple(models.values())


def endpoint(settings: Mapping[str, str], model: ChatModel, claude: bool) -> ChatEndpoint:
    config = dict(settings)
    if claude:
        config.update(EASEL_DIRECT_API_BASE_URL=settings.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com/v1'),
                      EASEL_DIRECT_API_KEY=settings.get('ANTHROPIC_API_KEY', settings.get('ANTHROPIC_AUTH_TOKEN', '')))
    config['EASEL_DIRECT_API_MODEL'] = model.runtime_model or model.name
    try:
        base, runtime, key = api_config(config)
    except DirectAPIError:
        raise GenerationError('chat_provider_configuration_invalid', 409) from None
    if claude and not base.endswith('/v1'):
        base += '/v1'
    return ChatEndpoint(base + ('/messages' if claude else '/chat/completions'), key, runtime, model.channel)
