"""Side-effect-free image settings shared by the CLI and generation service."""
from __future__ import annotations

from typing import Final, Literal
from urllib.parse import urlsplit

ENV_BASE_URL: Final = 'IMG_BASE_URL'
ENV_MODEL: Final = 'IMG_MODEL'
ENV_API_KEY: Final = 'IMG_API_KEY'
ENV_ALIASES: Final = {
    ENV_BASE_URL: ('OPENAI_BASE_URL', 'OPENAI_API_BASE', 'BASE_URL'),
    ENV_MODEL: ('OPENAI_IMAGE_MODEL', 'IMAGE_MODEL', 'OPENAI_MODEL'),
    ENV_API_KEY: ('OPENAI_API_KEY', 'API_KEY'),
}
MIME_MAP: Final = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                   'webp': 'image/webp', 'gif': 'image/gif'}


def detect_mode(base_url: str, explicit_mode: str | None) -> Literal['sync', 'async']:
    if explicit_mode == 'sync':
        return 'sync'
    if explicit_mode == 'async':
        return 'async'
    if urlsplit(base_url).hostname in {'api.apimart.ai', 'api.apib.ai', 'api.aiuxu.com', 'api.aishuch.com'}:
        return 'async'
    return 'sync'


def detect_provider(base_url: str) -> Literal['agnes', 'openai']:
    host = (urlsplit(base_url).hostname or '').lower()
    if any(host == domain or host.endswith('.' + domain) for domain in ('agnes-ai.cn', 'agnes-ai.com')):
        return 'agnes'
    return 'openai'
