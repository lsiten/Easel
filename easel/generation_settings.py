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
    description: str = ''
    required: bool = False
    default: str = ''
    section: str = ''


SETTINGS: Final = (
    GenerationSetting(
        name='VOLCENGINE_ACCESS_KEY_ID',
        label='火山引擎 VOD Access Key ID',
        secret=True,
        required=True,
        section='智能视频 / VOD',
        description='仅用于火山 VOD 的智能视频任务，例如翻译、擦除、高光、解说和轨道编辑；它不是 Seedance 生视频的 ARK Key。',
    ),
    GenerationSetting(
        name='VOLCENGINE_SECRET_ACCESS_KEY',
        label='火山引擎 VOD Secret Access Key',
        secret=True,
        required=True,
        section='智能视频 / VOD',
        description='与 Access Key ID 配对，用于签发火山 VOD OpenAPI 请求。',
    ),
    GenerationSetting(
        name='VOLCENGINE_SPACE_NAME',
        label='VOD 空间名称',
        required=True,
        section='智能视频 / VOD',
        description='输入素材上传、智能处理和结果文件所在的空间。',
    ),
    GenerationSetting(
        name='VOLCENGINE_VOD_BASE_URL',
        label='VOD OpenAPI 地址',
        default='https://vod.volcengineapi.com',
        section='智能视频 / VOD',
        description='一般留空使用官方默认地址；只有在代理或私有网关时才需要覆盖。',
    ),
    GenerationSetting(
        name='VOLCENGINE_REGION',
        label='火山引擎区域',
        default='cn-north-1',
        section='智能视频 / VOD',
        description='用于 VOD 请求签名，通常保持默认 cn-north-1。',
    ),
    GenerationSetting(
        name='VOLCENGINE_PLAYBACK_BASE_URL',
        label='VOD 播放域名',
        section='智能视频 / VOD',
        description='智能视频结果返回 VID 时，用于生成可下载的播放 URL；火山直接返回 Url 时可不填。',
    ),
    GenerationSetting(
        name='VOLCENGINE_URL_AUTH_PRIMARY_KEY',
        label='VOD 播放签名密钥',
        secret=True,
        section='智能视频 / VOD',
        description='与播放域名配对，用于生成带签名的临时播放 URL；火山直接返回 Url 时可不填。',
    ),
    GenerationSetting(
        name='EASEL_DRAFT_MODEL_RATES',
        label='模型费率 JSON',
        section='成本估算',
        description='只影响批量创作的成本预估，不参与模型请求或计费；按模型名配置 amount、currency 和 unit。',
    ),
    GenerationSetting(
        name='EASEL_DRAFT_ASR_MODEL',
        label='本地语音识别模型',
        section='语音转写',
        description='本地 Whisper 使用的模型名；云端转写 provider 的模型应在上方转写 provider 行配置。',
    ),
    GenerationSetting(
        name='EASEL_CHANNEL_MEDIA_BASE_URL',
        label='公开媒体 HTTPS 地址',
        section='外部素材访问',
        description='生成模型或发布平台需要读取本机参考素材时使用的公网 HTTPS 根地址；纯文字、无本机引用的生成不需要。',
    ),
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
