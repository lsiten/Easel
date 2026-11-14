"""Platform application configuration and executable Ego setup contracts."""
from collections.abc import Mapping
from pathlib import Path
import json
from typing import Final
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from easel.channels import ChannelError, definition
from easel.channels.manifest import describe_platform
from easel.channels.native_config import native_setting

PORTALS: Final = {
    'youtube': 'https://console.cloud.google.com/auth/clients',
    'google-business': 'https://console.cloud.google.com/auth/clients',
    'tiktok': 'https://developers.tiktok.com/portal/apps',
    'instagram': 'https://developers.facebook.com/apps/', 'facebook': 'https://developers.facebook.com/apps/',
    'threads': 'https://developers.facebook.com/apps/', 'twitter': 'https://developer.x.com/en/portal/dashboard',
    'linkedin': 'https://www.linkedin.com/developers/apps', 'pinterest': 'https://developers.pinterest.com/apps/',
    'bilibili-open': 'https://open.bilibili.com/', 'kwai-open': 'https://open.kuaishou.com/',
    'douyin-open': 'https://developer.open-douyin.com/', 'wechat-official-open': 'https://mp.weixin.qq.com/',
    'wechat-channels-open': 'https://channels.weixin.qq.com/', 'rednote-link': 'https://agora.xiaohongshu.com/',
}


class ApplicationUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    persona: str
    values: dict[str, SecretStr] = Field(min_length=1, max_length=8)


class ApplicationSetupRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    persona: str
    session_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')


class ApplicationStatus(BaseModel):
    model_config = ConfigDict(frozen=True)
    platform: str
    portal_url: str
    configured: bool
    fields: tuple[str, ...]
    present: dict[str, bool]
    connected: bool = False


def application_fields(platform: str, settings: Mapping[str, str]) -> tuple[str, ...]:
    spec = definition(platform)
    fields = ['CLIENT_ID', 'CLIENT_SECRET']
    mode = describe_platform(spec, settings)['auth_mode']
    if mode == 'oauth2':
        fields.append('REDIRECT_URI')
    if mode == 'miniapp_qr':
        fields.extend(('MINIAPP_CLIENT_ID', 'MINIAPP_CLIENT_SECRET'))
    if platform == 'rednote-link':
        fields.append('ACCESS_TOKEN_URL')
    if platform == 'wechat-official-open':
        fields.append('WEBHOOK_VERIFY_TOKEN')
    return tuple(fields)


def application_view(platform: str, settings: Mapping[str, str]) -> ApplicationStatus:
    fields = application_fields(platform, settings)
    return ApplicationStatus(platform=platform, portal_url=PORTALS[platform],
        configured=bool(describe_platform(definition(platform), settings)['configured']),
        fields=fields, present={name: bool(native_setting(settings, platform, name)) for name in fields})


def application_updates(platform: str, payload: ApplicationUpdate, settings: Mapping[str, str]) -> dict[str, str]:
    allowed = set(application_fields(platform, settings))
    if set(payload.values) - allowed:
        raise ChannelError(platform, 'application_field_unsupported')
    updates = {}
    for name, secret in payload.values.items():
        value = secret.get_secret_value().strip()
        if not value:
            continue
        if name.endswith('_URI') or name.endswith('_URL'):
            try:
                parsed = urlsplit(value)
                parsed.port
            except ValueError as error:
                raise ChannelError(platform, 'application_url_invalid') from error
            local = parsed.hostname in {'localhost', '127.0.0.1', '::1'}
            if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.scheme == 'http' and not local:
                raise ChannelError(platform, 'application_url_invalid')
            if name == 'REDIRECT_URI' and not parsed.path.endswith(f'/api/channels/{platform}/auth/callback'):
                raise ChannelError(platform, 'application_callback_path_invalid')
        updates['EASEL_' + platform.upper().replace('-', '_') + '_' + name] = value
    if not updates:
        raise ChannelError(platform, 'application_values_required')
    return updates


def setup_prompt(platform: str, persona: str, setup_id: str, base: str, env_path: Path, settings: Mapping[str, str]) -> str:
    fields = application_fields(platform, settings)
    callback_base = settings.get('EASEL_CHANNEL_MEDIA_BASE_URL', '').rstrip('/') or base.rstrip('/')
    contract = {'platform': platform, 'persona': persona, 'setup_id': setup_id,
        'portal_url': PORTALS[platform], 'application_name': f'Easel {persona}',
        'callback_uri': f'{callback_base}/api/channels/{platform}/auth/callback',
        'fields': fields, 'scopes': definition(platform).scopes,
        'save_url': f'{base.rstrip("/")}/api/channel-applications/{platform}',
        'progress_url': f'{base.rstrip("/")}/api/channel-applications/{platform}/setup/{setup_id}',
        'verify_url': f'{base.rstrip("/")}/api/channels/{platform}/accounts?persona={quote(persona)}'}
    return '\n'.join([
        '执行用户已授权的平台应用配置任务，完成配置并验证该画像的授权状态。',
        '先读取本机 ego-browser Skill 的 SKILL.md 并运行 ego-browser skill，按当前完整指南执行。',
        '全部浏览器操作使用 ego-browser nodejs 与该 Skill 的 taskSpace/page API，复用同一任务空间并保存其ID。',
        '读取当前页面后选择已有合适应用，或按真实控制台要求创建 Easel 应用并配置回调、所需产品及权限。',
        '登录、验证码、实名资料、协议确认、付费开通和平台审批由用户处理。需要用户时调用 task.handOff()，更新进度为 waiting_user 并在对话说明具体操作，等待用户回复后接管同一空间继续。',
        '浏览器现有业务登录与应用授权是可选的两条接入，按当前选中的应用授权任务处理。',
        '平台要求的公开域名、隐私政策、资质或真实资料缺失时，询问用户缺少的具体信息并保留当前进度。',
        '从控制台读取真实字段后直接在 ego-browser Node 变量中用 fetch POST save_url，JSON为 {persona,values}，values只使用fields内字段。',
        '密钥只在浏览器和服务端保存请求之间传递，工具输出、对话、截图和进度消息只显示字段是否配置。',
        f'本项目配置文件为 {env_path}，通过save_url保存会验证字段并原子更新它。',
        '保存后检查返回的configured。适用的OAuth平台再调用现有 /api/channels/{platform}/auth/start 并通过Ego完成用户授权；小程序扫码或Cookie绑定按现有平台入口处理。',
        '完成后GET verify_url读取服务器实际账号，以POST progress_url的 {persona,state:"complete",message:"..."} 请求核对配置和连接结果。只有服务器实际凭据决定连接状态。',
        '保存配置与账号连接分阶段汇报。遇到审批等待时保留waiting_user，并告知后续继续方式。',
        '任务结束按Ego Skill规范收尾任务空间。',
        json.dumps(contract, ensure_ascii=False),
    ])
