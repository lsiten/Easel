"""Capabilities reimplemented from AiToEarn source commit 4b59b10."""
from __future__ import annotations

from typing import Final, Mapping
from types import MappingProxyType

from easel.channels.models import PlatformDefinition, PlatformId
from easel.channels.native_definitions import NATIVE_DEFINITIONS

_DEFINITIONS = (
    PlatformDefinition('youtube', 'https://accounts.google.com/o/oauth2/v2/auth',
        'https://oauth2.googleapis.com/token', 'https://www.googleapis.com/youtube/v3',
        ('openid', 'profile', 'https://www.googleapis.com/auth/youtube.upload',
         'https://www.googleapis.com/auth/youtube.readonly', 'https://www.googleapis.com/auth/youtube.force-ssl',
         'https://www.googleapis.com/auth/yt-analytics.readonly'),
        pkce=True, publish_modes=('video',), max_text=5000, max_videos=1, completion='sync', works=True, analytics=True, engagement=True),
    PlatformDefinition('tiktok', 'https://www.tiktok.com/v2/auth/authorize/',
        'https://open.tiktokapis.com/v2/oauth/token/', 'https://open.tiktokapis.com/v2',
        ('user.info.basic', 'user.info.profile', 'user.info.stats', 'video.list', 'video.publish', 'video.upload'),
        scope_separator=',', token_auth='client_key', pkce=True, publish_modes=('image', 'video'), max_text=2200,
        max_images=35, max_videos=1, completion='polling', works=True, analytics=True),
    PlatformDefinition('instagram', 'https://www.instagram.com/oauth/authorize',
        'https://api.instagram.com/oauth/access_token', 'https://graph.instagram.com/v25.0',
        ('instagram_business_basic', 'instagram_business_content_publish', 'instagram_business_manage_comments', 'instagram_business_manage_insights'),
        scope_separator=',', publish_modes=('image', 'video'), max_text=2200, max_images=10, max_videos=1,
        completion='polling', works=True, analytics=True, engagement=True),
    PlatformDefinition('facebook', 'https://www.facebook.com/v25.0/dialog/oauth',
        'https://graph.facebook.com/v25.0/oauth/access_token', 'https://graph.facebook.com/v25.0',
        ('public_profile', 'pages_show_list', 'pages_read_engagement', 'pages_manage_posts', 'pages_manage_engagement', 'read_insights'),
        scope_separator=',', token_auth='graph', publish_modes=('text', 'image', 'video'), max_text=63206,
        max_images=10, max_videos=1, completion='sync', works=True, analytics=True, engagement=True),
    PlatformDefinition('threads', 'https://threads.net/oauth/authorize',
        'https://graph.threads.net/oauth/access_token', 'https://graph.threads.net/v1.0',
        ('threads_basic', 'threads_content_publish', 'threads_manage_insights', 'threads_read_replies', 'threads_manage_replies'),
        scope_separator=',', publish_modes=('text', 'image', 'video'), max_text=500, max_images=10,
        max_videos=1, completion='polling', works=True, analytics=True, engagement=True),
    PlatformDefinition('twitter', 'https://x.com/i/oauth2/authorize',
        'https://api.x.com/2/oauth2/token', 'https://api.x.com/2',
        ('tweet.read', 'tweet.write', 'users.read', 'offline.access', 'media.write', 'like.read', 'like.write', 'bookmark.read', 'bookmark.write', 'follows.read', 'follows.write'), token_auth='basic',
        pkce=True, publish_modes=('text', 'image', 'video'), max_text=280, max_images=4,
        max_videos=1, completion='sync', works=True, analytics=True, engagement=True),
    PlatformDefinition('linkedin', 'https://www.linkedin.com/oauth/v2/authorization',
        'https://www.linkedin.com/oauth/v2/accessToken', 'https://api.linkedin.com/rest',
        ('openid', 'profile', 'w_member_social'), publish_modes=('text', 'image', 'video'), max_text=3000,
        max_images=9, max_videos=1, completion='sync'),
    PlatformDefinition('pinterest', 'https://www.pinterest.com/oauth/',
        'https://api.pinterest.com/v5/oauth/token', 'https://api.pinterest.com/v5',
        ('user_accounts:read', 'boards:read', 'boards:write', 'pins:read', 'pins:write'), scope_separator=',', token_auth='basic',
        publish_modes=('image', 'video'), max_text=800, max_images=1, max_videos=1,
        completion='sync', works=True, analytics=True),
    PlatformDefinition('google-business', 'https://accounts.google.com/o/oauth2/v2/auth',
        'https://oauth2.googleapis.com/token', 'https://mybusinessaccountmanagement.googleapis.com/v1',
        ('openid', 'profile', 'https://www.googleapis.com/auth/business.manage'), pkce=True),
)
DEFINITIONS: Final[Mapping[PlatformId, PlatformDefinition]] = MappingProxyType({item.platform: item for item in (*_DEFINITIONS, *NATIVE_DEFINITIONS)})


def definition(platform: str) -> PlatformDefinition:
    from easel.channels.models import ChannelError
    result = DEFINITIONS.get(platform)
    if result is None:
        raise ChannelError(platform, 'unsupported_platform')
    return result
