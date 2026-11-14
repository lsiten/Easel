"""Verify publication from authenticated account analytics read-back."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Final, TypedDict
from urllib.parse import urlsplit, urlunsplit

from pydantic import JsonValue

from easel.publish_models import PublishRun


class ReadbackOutcome(str, Enum):
    VERIFIED = 'verified'
    UNVERIFIED = 'unverified'
    AMBIGUOUS = 'ambiguous'
    ERROR = 'error'


@dataclass(frozen=True, slots=True)
class ReadbackQuery:
    platform_work_id: str = ''
    permalink: str = ''


@dataclass(frozen=True, slots=True)
class ReadbackResult:
    outcome: ReadbackOutcome
    account_uid: str
    platform_work_id: str = ''
    permalink: str = ''
    reason: str = ''
    published_at: str = ''


class ObservedWork(TypedDict):
    id: str
    title: str
    published_at: JsonValue
    status: str
    url: str


class ReadbackObservation(TypedDict):
    platform: str
    loggedIn: bool
    account_uid: str
    notes: list[ObservedWork]


_WORK_HOSTS: Final = {
    'xiaohongshu': frozenset({'www.xiaohongshu.com', 'xiaohongshu.com'}),
    'douyin': frozenset({'www.douyin.com', 'douyin.com'}),
    'kuaishou': frozenset({'www.kuaishou.com', 'kuaishou.com'}),
    'zhihu': frozenset({'www.zhihu.com', 'zhihu.com', 'zhuanlan.zhihu.com'}),
    'bilibili': frozenset({'www.bilibili.com', 'bilibili.com'}),
    'wechat-oa': frozenset({'mp.weixin.qq.com'}),
    'weixin-channels': frozenset({'channels.weixin.qq.com'}),
    'csdn': frozenset({'blog.csdn.net'}),
    'juejin': frozenset({'juejin.cn'}),
    'youtube': frozenset({'www.youtube.com', 'youtube.com', 'youtu.be'}),
    'tiktok': frozenset({'www.tiktok.com', 'tiktok.com'}),
    'instagram': frozenset({'www.instagram.com', 'instagram.com'}),
    'facebook': frozenset({'www.facebook.com', 'facebook.com'}),
    'threads': frozenset({'www.threads.net', 'threads.net', 'www.threads.com', 'threads.com'}),
    'twitter': frozenset({'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'}),
    'linkedin': frozenset({'www.linkedin.com', 'linkedin.com'}),
    'pinterest': frozenset({'www.pinterest.com', 'pinterest.com'}),
    'bilibili-open': frozenset({'www.bilibili.com', 'bilibili.com'}),
    'douyin-open': frozenset({'www.douyin.com', 'douyin.com', 'www.iesdouyin.com', 'iesdouyin.com'}),
    'kwai-open': frozenset({'www.kuaishou.com', 'kuaishou.com'}),
    'wechat-official-open': frozenset({'mp.weixin.qq.com'}),
    'wechat-channels-open': frozenset({'channels.weixin.qq.com', 'weixin.qq.com'}),
    'rednote-link': frozenset({'www.xiaohongshu.com', 'xiaohongshu.com'}),
}


def _text(payload: Mapping[str, JsonValue], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if type(value) in (str, int) and str(value).strip():
            return str(value).strip()
    return ''


def normalize_verification_receipt(platform: str, receipt: Mapping[str, JsonValue]) -> ReadbackObservation:
    """Adapt the existing platform_readback receipt without weakening its verdict."""
    evidence = receipt.get('evidence')
    account = evidence.get('account') if isinstance(evidence, dict) else None
    uid = _text(account, 'platform_account_id') if isinstance(account, dict) else ''
    matched = receipt.get('matched')
    verified = receipt.get('outcome') == 'verified' and bool(uid) and isinstance(matched, dict)
    notes: list[ObservedWork] = []
    if verified and isinstance(matched, dict):
        notes.append({'id': _text(matched, 'platform_content_id'), 'title': _text(matched, 'title'),
                      'published_at': matched.get('published_at_ms'), 'status': _text(matched, 'status'),
                      'url': _text(matched, 'permalink', 'url')})
    return {'platform': platform, 'loggedIn': verified, 'account_uid': uid, 'notes': notes}


def canonical_work_url(platform: str, value: str) -> str:
    """Accept only a public work link from the declared platform."""
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return ''
    if parsed.scheme not in {'https', 'http'} or parsed.hostname not in _WORK_HOSTS.get(platform, frozenset()):
        return ''
    if parsed.username or parsed.password or port not in {None, 80, 443}:
        return ''
    # YouTube and WeChat encode the work identity in the query string.
    query = parsed.query if platform in {'youtube', 'wechat-oa', 'wechat-official-open'} else ''
    return urlunsplit(('https', parsed.hostname, parsed.path.rstrip('/'), query, ''))


def _work_time(note: Mapping[str, JsonValue]) -> datetime | None:
    value = next((note[key] for key in ('published_at', 'created_at', 'create_time', 'timestamp')
                  if note.get(key) is not None), None)
    try:
        if type(value) in (int, float):
            seconds = value / 1000 if value > 100_000_000_000 else value
            return datetime.fromtimestamp(seconds, timezone.utc)
        if isinstance(value, str):
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return stamp if stamp.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None
    return None


def _candidate_matches_intent(run: PublishRun, note: Mapping[str, JsonValue],
                              work_id: str, url: str) -> bool:
    if run.platform_work_id and run.platform_work_id == work_id:
        return True
    persisted_url = canonical_work_url(run.platform, run.permalink)
    if persisted_url and persisted_url == url:
        return True
    title = _text(run.payload, 'title')
    if not title or title != _text(note, 'title'):
        return False
    published = _work_time(note)
    created = datetime.fromisoformat(run.created_at.replace('Z', '+00:00'))
    return published is not None and published >= created


def verify_readback(run: PublishRun, payload: Mapping[str, JsonValue],
                    query: ReadbackQuery) -> ReadbackResult:
    """Compare server-collected account and exact work evidence to one run."""
    expected_uid = _text(run.payload, 'account_uid', 'accountUid')
    observed_uid = _text(payload, 'account_uid', 'accountUid', 'userId', 'platformUid')
    if payload.get('error'):
        return ReadbackResult(ReadbackOutcome.ERROR, observed_uid, reason='platform_read_failed')
    if payload.get('loggedIn', payload.get('logged_in')) is not True:
        return ReadbackResult(ReadbackOutcome.UNVERIFIED, observed_uid, reason='account_not_authenticated')
    if not expected_uid or not observed_uid or expected_uid != observed_uid:
        return ReadbackResult(ReadbackOutcome.UNVERIFIED, observed_uid, reason='account_identity_unverified')
    if payload.get('platform') != run.platform:
        return ReadbackResult(ReadbackOutcome.UNVERIFIED, observed_uid, reason='platform_mismatch')
    requested_id = query.platform_work_id.strip() or run.platform_work_id
    requested_url = canonical_work_url(run.platform, query.permalink or run.permalink)
    if query.permalink and not requested_url:
        return ReadbackResult(ReadbackOutcome.UNVERIFIED, observed_uid, reason='invalid_work_url')
    if not requested_id and not requested_url:
        return ReadbackResult(ReadbackOutcome.AMBIGUOUS, observed_uid, reason='exact_work_identity_required')
    notes = payload.get('notes', payload.get('works', []))
    if not isinstance(notes, list):
        return ReadbackResult(ReadbackOutcome.UNVERIFIED, observed_uid, reason='work_list_unavailable')
    candidates: list[tuple[str, str, str]] = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        if note.get('status') in {'draft', 'failed', 'deleted', 'reviewing', 'scheduled', 'private', 'prohibited'}:
            continue
        author_uid = _text(note, 'account_uid', 'accountUid', 'author_id', 'authorId')
        if author_uid and author_uid != observed_uid:
            continue
        url = canonical_work_url(run.platform, _text(note, 'url', 'permalink', 'share_url'))
        work_id = _text(note, 'platform_work_id', 'note_id', 'noteId', 'id', 'item_id', 'video_id')
        if not work_id and url and run.platform not in {'youtube', 'wechat-oa'}:
            work_id = urlsplit(url).path.rstrip('/').split('/')[-1]
        if requested_id and work_id != requested_id:
            continue
        if requested_url and url != requested_url:
            continue
        if not _candidate_matches_intent(run, note, work_id, url):
            continue
        observed_time = _work_time(note)
        published_at = observed_time.astimezone(timezone.utc).isoformat() if observed_time is not None else ''
        candidates.append((work_id, url, published_at))
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) > 1:
        return ReadbackResult(ReadbackOutcome.AMBIGUOUS, observed_uid, reason='multiple_work_matches')
    if not candidates:
        return ReadbackResult(ReadbackOutcome.UNVERIFIED, observed_uid, reason='work_not_observed')
    work_id, url, published_at = candidates[0]
    return ReadbackResult(ReadbackOutcome.VERIFIED, observed_uid, work_id, url, published_at=published_at)
