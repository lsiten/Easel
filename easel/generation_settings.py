"""Admin configuration metadata for migrated AI services."""
import base64
import binascii
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from easel.generation_models import GenerationError


class GenerationSetting(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    label: str
    secret: bool = False


SETTINGS: Final = (
    GenerationSetting(name='GEMINI_API_KEY', label='Gemini native API Key', secret=True),
    GenerationSetting(name='GEMINI_BASE_URL', label='Gemini native API URL'),
    GenerationSetting(name='IMG_PROVIDER', label='Image provider channel'),
    GenerationSetting(name='IMG_MODEL', label='Image model'),
    GenerationSetting(name='IMG_BASE_URL', label='Image API URL'),
    GenerationSetting(name='IMG_API_KEY', label='Image API Key', secret=True),
    GenerationSetting(name='VIDEO_MODEL', label='Video model'),
    GenerationSetting(name='VIDEO_BASE_URL', label='Video API URL'),
    GenerationSetting(name='VIDEO_API_KEY', label='Video API Key', secret=True),

    GenerationSetting(name='EASEL_DIRECT_API_BASE_URL', label='Chat API URL'),
    GenerationSetting(name='EASEL_DIRECT_API_KEY', label='Chat API Key', secret=True),
    GenerationSetting(name='EASEL_DIRECT_API_MODEL', label='Chat API model'),
    GenerationSetting(name='ANTHROPIC_BASE_URL', label='Anthropic API URL'),
    GenerationSetting(name='ANTHROPIC_API_KEY', label='Anthropic API Key', secret=True),
    GenerationSetting(name='ANTHROPIC_AUTH_TOKEN', label='Anthropic auth token', secret=True),
    GenerationSetting(name='GROK_API_KEY', label='Grok API Key', secret=True),
    GenerationSetting(name='GROK_BASE_URL', label='Grok API URL'),
    GenerationSetting(name='GROK_VIDEO_MODEL', label='Grok video model'),
    GenerationSetting(name='RELAY_API_KEY', label='Relay API Key', secret=True),
    GenerationSetting(name='RELAY_BASE_URL', label='Relay API URL'),
    GenerationSetting(name='RELAY_VIDEO_MODEL', label='Relay video model'),
    GenerationSetting(name='VOLCENGINE_ACCESS_KEY_ID', label='Volcengine Access Key ID', secret=True),
    GenerationSetting(name='VOLCENGINE_SECRET_ACCESS_KEY', label='Volcengine Secret Access Key', secret=True),
    GenerationSetting(name='VOLCENGINE_SPACE_NAME', label='Volcengine VOD space'),
    GenerationSetting(name='VOLCENGINE_VOD_BASE_URL', label='Volcengine VOD URL'),
    GenerationSetting(name='VOLCENGINE_REGION', label='Volcengine region'),
    GenerationSetting(name='VOLCENGINE_PLAYBACK_BASE_URL', label='Volcengine playback URL'),
    GenerationSetting(name='VOLCENGINE_URL_AUTH_PRIMARY_KEY', label='Volcengine playback signing key', secret=True),
    GenerationSetting(name='ANTHROPIC_MODEL', label='Anthropic chat API model'),
    GenerationSetting(name='EASEL_DRAFT_PLANNER_MODEL', label='Draft planner model'),
    GenerationSetting(name='EASEL_DRAFT_MODEL_RATES', label='Draft model rate JSON'),
    GenerationSetting(name='EASEL_DRAFT_ASR_MODEL', label='Cached local ASR model'),
    GenerationSetting(name='EASEL_CHANNEL_MEDIA_BASE_URL', label='Public HTTPS media URL'),
)


class GenerationSettingsUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    values: dict[str, SecretStr] = Field(min_length=1, max_length=len(SETTINGS))


def decoded_rates(value: str) -> str:
    """Keep raw JSON environment support and decode shell-safe saved settings."""
    if not value.startswith('base64:'):
        return value
    try:
        return base64.b64decode(value[7:], validate=True).decode('utf-8')
    except (binascii.Error, UnicodeError, ValueError) as error:
        raise GenerationError('generation_rates_encoding_invalid', 409) from error


def encoded_rates(value: str) -> str:
    return 'base64:' + base64.b64encode(value.encode('utf-8')).decode('ascii') if value else ''
