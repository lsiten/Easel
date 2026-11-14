"""Normalize official provider analytics into Easel's authoritative snapshot shape."""
from __future__ import annotations

from easel.channels.models import Credential, JsonDocument
from easel.channels.reads import _document, _rows

_ACCOUNT = {'subscriberCount': 'followers', 'follower_count': 'followers', 'followers_count': 'followers',
            'following_count': 'following', 'follows_count': 'following', 'media_count': 'posts', 'videoCount': 'posts', 'video_count': 'posts', 'pin_count': 'posts',
            'tweet_count': 'posts', 'viewCount': 'views', 'monthly_views': 'views', 'likes_count': 'likes'}
_WORK = {'viewCount': 'views', 'view_count': 'views', 'views': 'views', 'impression_count': 'impressions',
         'likeCount': 'likes', 'like_count': 'likes', 'likes': 'likes', 'commentCount': 'comments', 'comment_count': 'comments',
         'reply_count': 'comments', 'comments': 'comments', 'replies': 'comments', 'share_count': 'shares',
         'shares': 'shares', 'retweet_count': 'shares', 'reposts': 'shares', 'saved': 'collects', 'bookmark_count': 'collects',
         'post_impressions': 'impressions', 'post_engaged_users': 'engagements', 'post_clicks': 'clicks',
         'reach': 'reach', 'total_interactions': 'engagements', 'IMPRESSION': 'impressions', 'SAVE': 'collects',
         'PIN_CLICK': 'clicks', 'OUTBOUND_CLICK': 'outbound_clicks', 'VIDEO_MRC_VIEW': 'views'}
_WORK.update(followers_count='followers', profile_views='profile_views', website_clicks='website_clicks', quotes='quotes')
_ACCOUNT.update(follower='followers', following='following', arc_passed_total='posts', fans_count='followers')
_WORK.update(view='views', like='likes', coin='coins', favorite='collects', share='shares', reply='comments', danmaku='danmaku')


def _numbers(row: JsonDocument, names: dict[str, str]) -> JsonDocument:
    result: JsonDocument = {}
    for key, target in names.items():
        value = row.get(key)
        if value is None: continue
        if type(value) in (int, float): result[target] = value
        elif isinstance(value, str) and value.isdecimal(): result[target] = int(value)
    return result


def normalize_analytics(credential: Credential, payload: JsonDocument, work_id: str = '') -> JsonDocument:
    platform = credential.platform
    row, insights = payload, {}
    if platform == 'youtube':
        items = _rows(payload.get('items'))
        row = items[0] if items else payload
        statistics = _document(row.get('statistics'))
    elif platform == 'tiktok':
        data = _document(payload.get('data'))
        videos = _rows(data.get('videos'))
        row = videos[0] if work_id and videos else _document(data.get('user'))
        statistics = row
    elif platform == 'twitter':
        row = _document(payload.get('data'))
        statistics = _document(row.get('public_metrics'))
    elif platform == 'pinterest' and work_id:
        for group in payload.values():
            daily = _rows(_document(group).get('daily_metrics'))
            if daily: insights.update(_document(daily[-1].get('metrics')))
        statistics = insights
    else:
        statistics = _document(payload.get('profile')) or row
        for insight in _rows(payload.get('data')):
            values = _rows(insight.get('values'))
            value = values[-1].get('value') if values else _document(insight.get('total_value')).get('value')
            name = insight.get('name')
            if isinstance(name, str): insights[name] = value
        statistics = {**statistics, **insights}
    result: JsonDocument = {'loggedIn': True, 'accountUid': credential.account_uid, 'raw_response': payload}
    if work_id:
        metrics = {'views': None, 'likes': None, 'comments': None, 'shares': None, 'collects': None,
                   **_numbers(statistics, _WORK)}
        snippet = _document(row.get('snippet'))
        result.update(content_metrics=metrics, notes=[{'platform_work_id': work_id,
            'title': snippet.get('title') or row.get('title') or row.get('text') or row.get('caption') or '',
            'permalink': row.get('permalink') or row.get('share_url') or row.get('permalink_url') or '', 'metrics': metrics}])
    else:
        result['account_metrics'] = {'followers': None, 'following': None, 'likes': None, 'posts': None,
                                     **_numbers(statistics, _ACCOUNT), **_numbers(insights, _WORK)}
        result.update({key: result['account_metrics'][key] for key in ('followers', 'following', 'likes', 'posts')})
    return result


def analytics_history(credential: Credential, payload: JsonDocument, work_id: str = '') -> list[JsonDocument]:
    from datetime import datetime, timezone
    points: dict[str, JsonDocument] = {}
    for insight in _rows(payload.get('data')):
        name = insight.get('name')
        if not isinstance(name, str): continue
        for item in _rows(insight.get('values')):
            stamp = item.get('end_time')
            if isinstance(stamp, str): points.setdefault(stamp, {})[name] = item.get('value')
    if credential.platform == 'pinterest' and work_id:
        for group in payload.values():
            for item in _rows(_document(group).get('daily_metrics')):
                stamp = item.get('date')
                if isinstance(stamp, str): points.setdefault(stamp, {}).update(_document(item.get('metrics')))
    history = []
    for stamp, values in sorted(points.items()):
        try:
            instant = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            observed = instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant
        except ValueError:
            continue
        metrics = _numbers(values, {**_ACCOUNT, **_WORK})
        point: JsonDocument = {'accountUid': credential.account_uid, 'loggedIn': True, 'observed_at': observed.isoformat(), 'raw_response': values}
        if work_id:
            point.update(content_metrics=metrics, notes=[{'platform_work_id': work_id, 'metrics': metrics}])
        else:
            point.update(account_metrics=metrics, **{key: metrics.get(key) for key in ('followers', 'following', 'likes', 'posts')})
        history.append(point)
    return history
