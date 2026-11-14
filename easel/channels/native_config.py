"""Explicit native app configuration, with source-name aliases for browser coexistence."""
from collections.abc import Mapping

from pydantic import SecretStr

from easel.channels.models import ChannelError, OAuthApplication


def native_setting(settings: Mapping[str, str], platform: str, name: str, default: str = '') -> str:
    prefixes = ('EASEL_' + platform.upper().replace('-', '_') + '_',
                'EASEL_' + platform.removesuffix('-open').removesuffix('-link').upper().replace('-', '_') + '_')
    names = (name, *({'CLIENT_ID': ('APP_ID', 'APP_KEY'), 'CLIENT_SECRET': ('APP_SECRET',)}.get(name, ())))
    return next((settings[prefix + key] for prefix in prefixes for key in names if settings.get(prefix + key)), default)


def native_application(settings: Mapping[str, str], platform: str) -> OAuthApplication:
    client = native_setting(settings, platform, 'CLIENT_ID')
    secret = native_setting(settings, platform, 'CLIENT_SECRET')
    if not client or not secret: raise ChannelError(platform, 'native_application_not_configured', status=409)
    return OAuthApplication(client_id=client, client_secret=SecretStr(secret),
        redirect_uri=native_setting(settings, platform, 'REDIRECT_URI'),
        scopes=tuple(native_setting(settings, platform, 'SCOPES').replace(',', ' ').split()))
