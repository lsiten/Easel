"""Source-declared capabilities and editor constraints for platform selection."""
from collections.abc import Mapping

from easel.channels.models import PlatformDefinition
from easel.channels.options import option_fields
from easel.channels.rules import declared_media_rules
from easel.channels.native_config import native_setting

_NAMES = {'youtube': 'YouTube', 'tiktok': 'TikTok', 'instagram': 'Instagram', 'facebook': 'Facebook',
          'threads': 'Threads', 'twitter': 'X / Twitter', 'linkedin': 'LinkedIn', 'pinterest': 'Pinterest', 'google-business': 'Google Business'}
_NAMES.update({'bilibili-open': '哔哩哔哩开放平台', 'kwai-open': '快手开放平台', 'douyin-open': '抖音开放平台',
    'wechat-official-open': '微信公众号开放协议', 'wechat-channels-open': '微信视频号作品接入', 'rednote-link': '小红书应用分享'})


def describe_platform(spec: PlatformDefinition, settings: Mapping[str, str]) -> dict:
    auth_mode = spec.auth_mode
    if spec.platform == 'douyin-open' and settings.get('EASEL_DOUYIN_OPEN_AUTH_TYPE', 'qr').lower() == 'oauth2': auth_mode = 'oauth2'
    config_keys = ('MINIAPP_CLIENT_ID', 'MINIAPP_CLIENT_SECRET') if auth_mode == 'miniapp_qr' else ('CLIENT_ID', 'CLIENT_SECRET')
    if spec.platform == 'rednote-link': config_keys += ('ACCESS_TOKEN_URL',)
    return {'platform': spec.platform, 'display_name': _NAMES[spec.platform],
        'publish_modes': spec.publish_modes, 'completion': spec.completion, 'works': spec.works,
        'analytics': spec.analytics, 'engagement': spec.engagement,
        'auth_mode': auth_mode, 'app_handoff_supported': spec.completion == 'user_handoff',
        'configured': all(native_setting(settings, spec.platform, key) for key in config_keys)
            and (auth_mode != 'oauth2' or bool(native_setting(settings, spec.platform, 'REDIRECT_URI'))),
        'account_binding': 'cookie' if spec.platform == 'wechat-channels-open' else 'handoff' if spec.platform == 'rednote-link' else auth_mode,
        'work_list_supported': spec.works and spec.platform not in {'douyin-open', 'wechat-channels-open', 'rednote-link'},
        'work_link_requires_credential': spec.platform not in {'douyin-open', 'bilibili-open', 'kwai-open', 'rednote-link'},
        'work_detail_supported': spec.works and spec.platform != 'rednote-link',
        'cover_supported': spec.platform in {'youtube', 'instagram', 'pinterest', 'tiktok', 'bilibili-open', 'kwai-open', 'wechat-official-open'},
        'cover_required': spec.platform in {'kwai-open', 'wechat-official-open'},
        'limits': {'max_title': {'youtube': 100, 'pinterest': 100, 'linkedin': 200, 'twitter': 0,
            'bilibili-open': 79, 'kwai-open': 30, 'douyin-open': 30, 'wechat-official-open': 64, 'wechat-channels-open': 16, 'rednote-link': 20}.get(spec.platform),
            'max_text': spec.max_text, 'max_images': spec.max_images, 'max_videos': spec.max_videos},
        'media_rules': declared_media_rules(spec.platform), 'option_fields': option_fields(spec.platform),
        'update_supported': spec.platform in {'youtube', 'pinterest'},
        'webhook_supported': spec.platform in {'youtube', 'tiktok', 'instagram', 'facebook', 'linkedin', 'bilibili-open', 'douyin-open', 'wechat-official-open'}}
