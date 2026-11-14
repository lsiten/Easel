"""Read-only platform capability projection for Easel integrations."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

PublishMode = Literal["none", "manual_only", "sync", "async", "user_action"]
AnalyticsScope = Literal["none", "account", "work"]


@dataclass(frozen=True, slots=True)
class MediaConstraints:
    requires_media: bool = False
    video_only: bool = False
    max_items: int = 32


@dataclass(frozen=True, slots=True)
class PlatformCapability:
    platform_id: str
    auth_mode: str
    publish_mode: PublishMode
    analytics_scope: AnalyticsScope
    engagement_scope: str
    media_constraints: MediaConstraints
    data_source: str
    contract_version: int = 1
    side_effect_class: str = "none"


_MEDIA_REQUIRED = frozenset({"xiaohongshu", "douyin", "kuaishou", "weixin-channels", "bilibili", "wechat-oa"})
_VIDEO_ONLY = frozenset({"douyin", "weixin-channels", "bilibili"})
_ASYNC = frozenset({"douyin"})
_ANALYTICS = frozenset({"xiaohongshu", "douyin", "kuaishou", "zhihu", "weixin-channels", "bilibili", "wechat-oa", "csdn", "juejin"})


def project_capabilities(
    login_runners: Mapping[str, Mapping[str, object]],
    analytics_platforms: frozenset[str] | set[str] | None = None,
) -> Mapping[str, PlatformCapability]:
    """Project existing runner metadata into a read-only capability view.

    The caller remains the source of truth for runnable adapters. This function only
    describes them; it never stores credentials or grants side effects.
    """
    analytics = _ANALYTICS if analytics_platforms is None else frozenset(analytics_platforms)
    result: dict[str, PlatformCapability] = {}
    for platform, config in login_runners.items():
        backend = str(config.get("backend", "unsupported"))
        supported = backend != "unsupported"
        if not supported:
            publish_mode: PublishMode = "none"
        elif platform in _ASYNC:
            publish_mode = "async"
        elif platform in {"wechat-oa"}:
            publish_mode = "sync"
        else:
            publish_mode = "sync"
        auth_mode = "credentials" if backend == "wechat-oa" else "browser_profile"
        result[platform] = PlatformCapability(
            platform_id=platform,
            auth_mode=auth_mode,
            publish_mode=publish_mode,
            analytics_scope="account" if platform in analytics else "none",
            engagement_scope="account" if platform in analytics else "none",
            media_constraints=MediaConstraints(
                requires_media=platform in _MEDIA_REQUIRED,
                video_only=platform in _VIDEO_ONLY,
            ),
            data_source="account_stats" if platform in analytics else "none",
            side_effect_class="external_publish" if publish_mode in {"sync", "async"} else "none",
        )
    return MappingProxyType(result)


def official_capabilities() -> Mapping[str, PlatformCapability]:
    from easel.channels import DEFINITIONS
    return MappingProxyType({platform: PlatformCapability(
        platform_id=platform, auth_mode=spec.auth_mode,
        publish_mode=('user_action' if spec.completion == 'user_handoff' else 'async' if spec.completion == 'polling' else 'sync') if spec.publish_modes else 'none',
        analytics_scope=('work' if spec.works else 'account') if spec.analytics else 'none',
        engagement_scope='work' if spec.engagement else 'none',
        media_constraints=MediaConstraints(
            requires_media=bool(spec.publish_modes) and 'text' not in spec.publish_modes,
            video_only=spec.publish_modes == ('video',), max_items=max(spec.max_images, spec.max_videos)),
        data_source='official_api', side_effect_class='external_publish' if spec.publish_modes else 'none',
    ) for platform, spec in DEFINITIONS.items()})
