"""Explicit engagement operations from the upstream engagement providers."""
from __future__ import annotations

from urllib.parse import quote

from easel.channels.models import ChannelError, Credential, JsonDocument
from easel.channels.reads import ChannelReads, _document, _rows


def engagement_action(reader: ChannelReads, credential: Credential, action: str, target_id: str, *, text: str = '') -> JsonDocument:
    if not target_id or len(target_id) > 512:
        raise ChannelError(reader.spec.platform, 'engagement_target_invalid')
    target, uid = quote(target_id, safe=''), quote(credential.account_uid, safe='')
    match reader.spec.platform:
        case 'facebook' if action in {'like', 'unlike'}:
            return reader.call('POST' if action == 'like' else 'DELETE', f'/{target}/likes', credential).data
        case 'threads' if action in {'hide_reply', 'unhide_reply'}:
            return reader.call('POST', f'/{target}/manage_reply', credential,
                               params={'hide': 'true' if action == 'hide_reply' else 'false'}).data
        case 'youtube' if action == 'reply':
            if not text.strip(): raise ChannelError('youtube', 'comment_empty')
            return reader.call('POST', '/comments', credential, params={'part': 'snippet'},
                body={'snippet': {'parentId': target_id, 'textOriginal': text}}).data
        case 'instagram' if action == 'reply':
            if not text.strip(): raise ChannelError('instagram', 'comment_empty')
            return reader.call('POST', f'/{target}/replies', credential, body={'message': text}).data
        case 'twitter':
            match action:
                case 'like' | 'bookmark' | 'repost' | 'follow':
                    edge = {'like': 'likes', 'bookmark': 'bookmarks', 'repost': 'retweets', 'follow': 'following'}[action]
                    key = 'target_user_id' if action == 'follow' else 'tweet_id'
                    return reader.call('POST', f'/users/{uid}/{edge}', credential, body={key: target_id}).data
                case 'unlike' | 'remove_bookmark' | 'undo_repost' | 'unfollow':
                    edge = {'unlike': 'likes', 'remove_bookmark': 'bookmarks', 'undo_repost': 'retweets', 'unfollow': 'following'}[action]
                    return reader.call('DELETE', f'/users/{uid}/{edge}/{target}', credential).data
                case 'quote':
                    if not text.strip(): raise ChannelError('twitter', 'comment_empty')
                    return reader.call('POST', '/tweets', credential, body={'text': text, 'quote_tweet_id': target_id}).data
                case _:
                    raise ChannelError('twitter', 'engagement_action_unsupported', status=405)
        case _:
            raise ChannelError(reader.spec.platform, 'engagement_action_unsupported', status=405)


def delete_comment(reader: ChannelReads, credential: Credential, comment_id: str) -> JsonDocument:
    target = quote(comment_id, safe='')
    match reader.spec.platform:
        case 'youtube':
            return reader.call('DELETE', '/comments', credential, params={'id': comment_id}).data
        case 'facebook' | 'instagram' | 'threads':
            return reader.call('DELETE', '/' + target, credential).data
        case 'twitter':
            return reader.call('DELETE', '/tweets/' + target, credential).data
        case 'linkedin':
            return reader.call('DELETE', '/socialActions/' + target, credential).data
        case _:
            raise ChannelError(reader.spec.platform, 'delete_comment_unsupported', status=405)


def assert_reply_target(reader: ChannelReads, credential: Credential, work_id: str, reply_id: str) -> None:
    from easel.channels.actions import comments
    cursor, seen = '', set()
    while True:
        payload = comments(reader, credential, work_id, cursor, limit=100)
        rows = _rows(payload.get('items') or payload.get('data') or payload.get('elements'))
        for row in rows:
            candidates = [row, _document(_document(row.get('snippet')).get('topLevelComment')),
                          *_rows(_document(row.get('replies')).get('comments')),
                          *_rows(_document(row.get('replies')).get('data'))]
            if any(str(item.get('id') or item.get('entity') or '') == reply_id for item in candidates): return
        after = payload.get('nextPageToken') or _document(payload.get('meta')).get('next_token') or _document(_document(payload.get('paging')).get('cursors')).get('after')
        if not isinstance(after, str) or not after or after in seen: break
        seen.add(after)
        cursor = after
    raise ChannelError(reader.spec.platform, 'reply_parent_not_in_work', status=403)
