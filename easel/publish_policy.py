"""Shared live publishing policy for web workers and local browser commits."""
from collections.abc import Mapping

from easel.manual_publish import publishing_mode


def publishing_allowed(platform: str, settings: Mapping[str, str]) -> bool:
    disabled = {item.strip().lower() for item in settings.get('EASEL_PUBLISH_DISABLED_PLATFORMS', '').split(',') if item.strip()}
    return publishing_mode(settings) == 'automated' and platform.lower() not in disabled
