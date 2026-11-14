"""Pinned source draft platform compatibility rules, evaluated on generated content."""
from __future__ import annotations
from typing import Final
from pydantic import BaseModel, ConfigDict
from easel.generation_draft_models import DraftJob


class Limits(BaseModel):
    model_config = ConfigDict(frozen=True)
    title_max: int | None = None
    title_required: bool = False
    desc_max: int | None = None
    desc_required: bool = False
    topics_min: int = 0
    topics_max: int | None = None
    video: bool = False
    duration_min: int | None = None
    duration_max: int | None = None
    ratios: tuple[str, ...] = ()
    image: bool = False
    image_max: int | None = None


RULES: Final = {
    'tiktok': Limits(desc_max=2200, topics_max=5, video=True, duration_min=3, duration_max=600, image=True, image_max=10),
    'instagram': Limits(desc_max=2200, video=True, duration_min=5, duration_max=900, image=True, image_max=10),
    'douyin': Limits(title_max=30, topics_max=5, video=True, duration_max=900, ratios=('9:16','16:9','1:1'), image=True, image_max=9),
    'bilibili': Limits(title_max=80, title_required=True, desc_max=250, topics_max=10, topics_min=1, video=True),
    'youtube': Limits(title_max=100, title_required=True, desc_max=5000, desc_required=True, video=True, duration_max=43200),
    'twitter': Limits(desc_max=280, desc_required=True, image=True, image_max=4),
    'facebook': Limits(desc_max=5000, video=True, duration_min=3, duration_max=14400, image=True, image_max=10),
    'threads': Limits(desc_max=500, desc_required=True, video=True, duration_max=300, image=True, image_max=20),
    'pinterest': Limits(title_required=True, video=True, duration_min=4, duration_max=15, image=True),
    'KWAI': Limits(topics_max=4, video=True, duration_min=15, duration_max=180, ratios=('9:16',)),
    'xhs': Limits(desc_max=1000, video=True, duration_max=900, ratios=('9:16','3:4','1:1','16:9'), image=True, image_max=9),
    'wxGzh': Limits(image=True),
    'wxSph': Limits(video=True),
    'linkedin': Limits(title_max=200, desc_max=3000, video=True, image=True),
}


def compatible_platforms(job: DraftJob) -> tuple[str, ...]:
    if job.plan is None:
        return ()
    plan, request = job.plan, job.request
    # JS String.length counts UTF-16 units; preserve source limit behavior for emoji.
    title_length = len(plan.title.encode('utf-16-le')) // 2
    desc_length = len(plan.description.encode('utf-16-le')) // 2
    result = []
    for name, rule in RULES.items():
        if rule.title_required and not plan.title.strip() or rule.desc_required and not plan.description.strip():
            continue
        if rule.title_max is not None and title_length > rule.title_max or rule.desc_max is not None and desc_length > rule.desc_max:
            continue
        if len(plan.topics) < rule.topics_min or rule.topics_max is not None and len(plan.topics) > rule.topics_max:
            continue
        if request.kind == 'image-text':
            if not rule.image or rule.image_max is not None and request.imageCount > rule.image_max:
                continue
        else:
            if not rule.video:
                continue
            duration = request.duration if request.kind == 'video' else None
            ratio = request.aspectRatio if request.kind == 'video' else None
            if duration is not None and (rule.duration_min is not None and duration < rule.duration_min or rule.duration_max is not None and duration > rule.duration_max):
                continue
            if ratio and rule.ratios and ratio not in rule.ratios:
                continue
        result.append(name)
    return tuple(result)


def local_platforms(platforms: tuple[str, ...]) -> tuple[str, ...]:
    aliases = {'xhs': 'xiaohongshu', 'wxSph': 'weixin-channels', 'KWAI': 'kuaishou', 'wxGzh': 'wechat-oa'}
    return tuple(dict.fromkeys(aliases.get(platform, platform) for platform in platforms))
