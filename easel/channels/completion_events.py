"""Authenticated notification hints for server-side owned publication read-back."""
from __future__ import annotations
import math
import time

from pydantic import TypeAdapter, ValidationError
from cryptography.fernet import InvalidToken

from easel.channels.models import JsonDocument
from easel.channels.reads import _document, _rows
from easel.channels.store import CredentialStore

_DOCUMENT = TypeAdapter(JsonDocument)


def normalize_completion_events(platform: str, payload: JsonDocument) -> list[JsonDocument]:
    events: list[JsonDocument] = []
    def append(account: str, value: JsonDocument, event_type: str = '') -> None:
        external = str(value.get('publish_id') or value.get('container_id') or '')
        identity = str(value.get('post_id') or value.get('media_id') or value.get('story_id') or value.get('video_id') or value.get('photo_id') or value.get('entityUrn') or value.get('entity') or value.get('id') or '')
        candidates = list(dict.fromkeys(str(value[key]) for key in ('publish_id', 'container_id', 'post_id', 'media_id', 'story_id', 'video_id', 'photo_id', 'entityUrn', 'entity', 'id') if value.get(key)))
        if not account or not candidates: return
        events.append({'platform': platform, 'account_uid': account, 'external_request_id': external,
            'platform_work_id': identity, 'candidate_ids': candidates, 'event_type': event_type,
            'status': value.get('status') or event_type, 'permalink': value.get('permalink') or value.get('permalink_url') or '',
            'occurred_at': value.get('create_time') or value.get('time') or value.get('lastModifiedAt')})
    if platform == 'youtube':
        for entry in _rows(payload.get('entries')):
            append(str(entry.get('channel_id') or ''), {'video_id': entry.get('video_id')}, 'video_notification')
    elif platform == 'tiktok':
        content = payload.get('content')
        try:
            value = _DOCUMENT.validate_json(content) if isinstance(content, str) else _document(content)
        except ValidationError:
            value = {}
        append(str(payload.get('user_openid') or ''), {**value, 'create_time': payload.get('create_time')}, str(payload.get('event') or ''))
    elif platform in {'instagram', 'facebook'}:
        for entry in _rows(payload.get('entry')):
            for change in _rows(entry.get('changes')):
                value = _document(change.get('value'))
                if value.get('comment_id') or value.get('item') == 'comment': continue
                if platform == 'instagram' and change.get('field') != 'media': continue
                append(str(entry.get('id') or ''), {**value, 'time': entry.get('time')}, str(change.get('field') or ''))
    elif platform == 'linkedin':
        owner = str(payload.get('owner') or '').removeprefix('urn:li:person:')
        for event in _rows(payload.get('events')): append(owner, event, str(event.get('eventType') or ''))
    return events


def scope_event_payload(platform: str, payload: JsonDocument, account_uid: str) -> JsonDocument:
    value = dict(payload)
    value['_completion_events'] = [event for event in _rows(payload.get('_completion_events')) if event.get('account_uid') == account_uid]
    if platform in {'facebook', 'instagram'}:
        value['entry'] = [entry for entry in _rows(payload.get('entry')) if str(entry.get('id')) == account_uid]
    if platform == 'youtube':
        value['entries'] = [entry for entry in _rows(payload.get('entries')) if entry.get('channel_id') == account_uid]
        value['channel_ids'] = [account_uid]
        value['video_ids'] = [entry['video_id'] for entry in _rows(value['entries'])]
    return value


def delivery_schema(store: CredentialStore) -> None:
    with store.connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS webhook_deliveries(
            digest TEXT NOT NULL,persona TEXT NOT NULL,platform TEXT NOT NULL,account_uid TEXT NOT NULL,
            processed INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0,error_code TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(digest,persona,platform,account_uid))''')


def _delivery(store: CredentialStore, event_id: str, persona: str, platform: str, account_uid: str,
              processed: bool, next_attempt: float, error_code: str) -> bool:
    delivery_schema(store)
    with store.connection() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='webhook_events'").fetchone() is None: return False
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM webhook_events WHERE digest=? AND persona=? AND platform=? AND account_uid=?',
                      (event_id, persona, platform, account_uid)).fetchone() is None: return False
        db.execute('''INSERT INTO webhook_deliveries VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(digest,persona,platform,account_uid) DO UPDATE SET
            processed=excluded.processed,next_attempt=excluded.next_attempt,error_code=excluded.error_code
            WHERE webhook_deliveries.processed=0''',
            (event_id, persona, platform, account_uid, int(processed), next_attempt, error_code[:200]))
    return True


def ack_verified_event(store: CredentialStore, event_id: str, persona: str, platform: str, account_uid: str) -> bool:
    return _delivery(store, event_id, persona, platform, account_uid, True, 0, '')


def defer_verified_event(store: CredentialStore, event_id: str, persona: str, platform: str, account_uid: str, *,
                          retry_after: float = 60, error_code: str = '') -> bool:
    if not math.isfinite(retry_after) or retry_after < 0: raise ValueError('retry_after invalid')
    return _delivery(store, event_id, persona, platform, account_uid, False, time.time() + retry_after, error_code)


def load_verified_events(store: CredentialStore, *, limit: int = 100, now: float | None = None) -> list[JsonDocument]:
    delivery_schema(store)
    with store.connection() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='webhook_events'").fetchone() is None: return []
        rows = db.execute('''SELECT e.* FROM webhook_events e LEFT JOIN webhook_deliveries d
            ON (e.digest=d.digest AND e.persona=d.persona AND e.platform=d.platform AND e.account_uid=d.account_uid)
            WHERE COALESCE(d.processed,0)=0 AND COALESCE(d.next_attempt,0)<=?
            ORDER BY e.received_at,e.digest,e.persona,e.account_uid LIMIT ?''',
            (time.time() if now is None else now, min(max(limit, 1), 1000))).fetchall()
    result = []
    for row in rows:
        try:
            payload = _DOCUMENT.validate_json(store.cipher.decrypt(row['encrypted']))
            events = payload.get('_completion_events') if '_completion_events' in payload else normalize_completion_events(row['platform'], payload)
            if not isinstance(events, list) or any(not isinstance(event, dict) for event in events): raise ValueError('event format')
            events = [event for event in events if event.get('account_uid') == row['account_uid'] and event.get('platform') == row['platform']]
        except (InvalidToken, ValidationError, ValueError, TypeError):
            _delivery(store, row['digest'], row['persona'], row['platform'], row['account_uid'], True, 0, 'inbox_integrity_failed')
            continue
        result.append({'event_id': row['digest'], 'persona': row['persona'], 'platform': row['platform'],
            'account_uid': row['account_uid'], 'received_at': row['received_at'], 'events': events})
    return result
