"""Registered MIT native protocols retain separate IDs from browser publishers."""
from easel.channels.models import PlatformDefinition

NATIVE_DEFINITIONS = (
    PlatformDefinition('bilibili-open', 'https://account.bilibili.com/pc/account-pc/auth/oauth',
        'https://api.bilibili.com/x/account-oauth2/v1/token', 'https://member.bilibili.com', (),
        publish_modes=('video',), max_text=249, max_videos=1, completion='polling', works=True, analytics=True),
    PlatformDefinition('kwai-open', 'https://open.kuaishou.com/oauth2/authorize',
        'https://open.kuaishou.com/oauth2/access_token', 'https://open.kuaishou.com',
        ('user_info', 'user_video_publish', 'user_video_info'), scope_separator=',',
        publish_modes=('video',), max_text=1000, max_videos=1, completion='polling', works=True, analytics=True),
    PlatformDefinition('douyin-open', 'https://open.douyin.com/platform/oauth/connect/',
        'https://open.douyin.com/oauth/access_token/', 'https://open.douyin.com',
        ('user_info', 'video.list', 'video.data'), scope_separator=',', token_auth='client_key',
        publish_modes=('image', 'video'), max_text=1000, max_images=12, max_videos=1,
        completion='user_handoff', works=True, auth_mode='miniapp_qr'),
    PlatformDefinition('wechat-official-open', 'https://open.weixin.qq.com/connect/oauth2/authorize',
        'https://api.weixin.qq.com/sns/oauth2/access_token', 'https://api.weixin.qq.com', ('snsapi_userinfo',),
        publish_modes=('text', 'image'), max_text=20000, max_images=20, completion='polling'),
    PlatformDefinition('wechat-channels-open', '', '', 'https://api.weixin.qq.com', (),
        publish_modes=('video',), max_text=1000, max_images=9, max_videos=1,
        completion='user_handoff', works=True, auth_mode='app_handoff'),
    PlatformDefinition('rednote-link', '', '', '', (), publish_modes=('image', 'video'), max_text=1000,
        max_images=9, max_videos=1, completion='user_handoff', works=True, auth_mode='app_handoff'),
)
NATIVE_IDS = frozenset(spec.platform for spec in NATIVE_DEFINITIONS)
