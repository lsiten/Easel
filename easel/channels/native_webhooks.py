"""Native signatures and encrypted accountless events awaiting server receipt matching."""
from collections.abc import Mapping, Sequence
import hashlib
import hmac
import json
import re
import time
import xml.etree.ElementTree as ET

from cryptography.fernet import InvalidToken
from pydantic import TypeAdapter, ValidationError

from easel.channels.models import ChannelError, JsonDocument
from easel.channels.native_config import native_setting
from easel.channels.reads import _document

_DOCUMENT = TypeAdapter(JsonDocument)
NATIVE_WEBHOOKS = {'bilibili-open', 'douyin-open', 'wechat-official-open'}


def xml_payload(body: bytes) -> JsonDocument:
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper(): raise ChannelError('wechat-official-open', 'webhook_body_invalid')
    try:
        root = ET.fromstring(body)
        return {element.tag: element.text or '' for element in root}
    except ET.ParseError as error:
        raise ChannelError('wechat-official-open', 'webhook_body_invalid') from error


def verify_native(platform: str, body: bytes, headers: Mapping[str, str], query: Mapping[str, str], settings: Mapping[str, str]) -> bool:
    if len(body) > 1024**2: return False
    values = {key.lower(): value for key, value in headers.items()}
    secret = native_setting(settings, platform, 'CLIENT_SECRET')
    if platform == 'bilibili-open':
        names = ('x-bili-accesskeyid', 'x-bili-content-md5', 'x-bili-signature-method', 'x-bili-signature-nonce', 'x-bili-signature-version', 'x-bili-timestamp')
        if not secret or any(not values.get(key) for key in names): return False
        if values[names[0]] != native_setting(settings, platform, 'CLIENT_ID') or not hmac.compare_digest(values[names[1]].encode(), hashlib.md5(body).hexdigest().encode()): return False
        expected = hmac.new(secret.encode(), '\n'.join(key + ':' + values[key] for key in names).encode(), hashlib.sha256).hexdigest()
        supplied = re.sub(r'^HMAC-SHA256\s+', '', values.get('authorization', ''), flags=re.IGNORECASE)
        return hmac.compare_digest(supplied.encode(), expected.encode())
    if platform == 'douyin-open':
        supplied = values.get('x-douyin-signature') or values.get('x-open-signature') or values.get('x-tt-signature') or query.get('signature', '')
        supplied = re.sub(r'^(sha1|sha256)=', '', supplied)
        return bool(secret and supplied) and (hmac.compare_digest(supplied.encode(), hashlib.sha1(secret.encode() + body).hexdigest().encode())
            or hmac.compare_digest(supplied.encode(), hmac.new(secret.encode(), body, hashlib.sha256).hexdigest().encode()))
    if platform == 'wechat-official-open':
        token = native_setting(settings, platform, 'WEBHOOK_VERIFY_TOKEN') or native_setting(settings, platform, 'TOKEN')
        timestamp, nonce = query.get('timestamp', ''), query.get('nonce', '')
        signature = query.get('signature') or query.get('msg_signature') or ''
        if not token or not timestamp or not nonce or not signature: return False
        encrypted = xml_payload(body).get('Encrypt') if body else ''
        expected = hashlib.sha1(''.join(sorted([token, timestamp, nonce, *([str(encrypted)] if encrypted else [])])).encode()).hexdigest()
        return hmac.compare_digest(signature.encode(), expected.encode())
    return False


def native_event(platform: str, body: bytes) -> tuple[JsonDocument, list[str]]:
    try:
        payload = xml_payload(body) if platform == 'wechat-official-open' else _DOCUMENT.validate_json(body)
    except ValidationError as error:
        raise ChannelError(platform, 'webhook_body_invalid') from error
    content = _document(payload.get('content'))
    candidates = list(dict.fromkeys(str(content[key]) for key in ('share_id', 'resource_id', 'video_id', 'item_id', 'publish_id') if content.get(key)))
    return payload, candidates


def schema(store) -> None:
    with store.connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS native_webhook_inbox(
            digest TEXT NOT NULL,platform TEXT NOT NULL,client_id TEXT NOT NULL,received_at REAL NOT NULL,
            encrypted BLOB NOT NULL,processed INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0,
            error_code TEXT NOT NULL DEFAULT '',PRIMARY KEY(digest,platform,client_id))''')


def save_native_event(store, platform: str, body: bytes, settings: Mapping[str, str]) -> JsonDocument:
    payload, candidates = native_event(platform, body)
    schema(store)
    client_id = native_setting(settings, platform, 'CLIENT_ID')
    digest = hashlib.sha256(body).hexdigest()
    encrypted = store.cipher.encrypt(_DOCUMENT.dump_json({'payload': payload, 'candidate_ids': candidates}))
    with store.connection() as db:
        db.execute('INSERT OR IGNORE INTO native_webhook_inbox(digest,platform,client_id,received_at,encrypted) VALUES(?,?,?,?,?)',
            (digest, platform, client_id, time.time(), encrypted))
    return {'event_id': digest, 'platform': platform, 'client_id': client_id, 'candidate_ids': candidates}


def load_native_webhook_events(store, *, limit: int = 100, now: float | None = None) -> list[JsonDocument]:
    schema(store)
    result = []
    with store.connection() as db:
        rows = db.execute('SELECT * FROM native_webhook_inbox WHERE processed=0 AND next_attempt<=? ORDER BY received_at,digest LIMIT ?',
            (time.time() if now is None else now, min(1000, max(1, limit)))).fetchall()
        for row in rows:
            try:
                value = _DOCUMENT.validate_json(store.cipher.decrypt(row['encrypted']))
                ids = value.get('candidate_ids')
                if not isinstance(value.get('payload'), dict) or not isinstance(ids, list) or any(not isinstance(item, str) or len(item) > 1024 for item in ids): raise ValueError('schema')
                result.append({'event_id': row['digest'], 'platform': row['platform'], 'client_id': row['client_id'],
                    'received_at': row['received_at'], **value})
            except (InvalidToken, ValidationError, ValueError):
                db.execute('UPDATE native_webhook_inbox SET processed=1,error_code=? WHERE digest=? AND platform=? AND client_id=?',
                    ('inbox_integrity_failed', row['digest'], row['platform'], row['client_id']))
    return result


def finish_native_webhook_event(store, event_id: str, platform: str, client_id: str, *, processed: bool = True, retry_after: float = 60, error_code: str = '') -> bool:
    import math
    if not math.isfinite(retry_after) or retry_after < 0: raise ValueError('retry_after invalid')
    schema(store)
    with store.connection() as db:
        return db.execute('UPDATE native_webhook_inbox SET processed=?,next_attempt=?,error_code=? WHERE digest=? AND platform=? AND client_id=? AND processed=0',
            (int(processed), 0 if processed else time.time() + retry_after, error_code[:200], event_id, platform, client_id)).rowcount == 1


def process_pending_app_events(store, settings: Mapping[str, str], resolver, *, limit: int = 100) -> int:
    """Bind accountless signed callbacks only to server-recorded publication identities."""
    from easel.channels.webhook_router import _schema
    _schema(store)
    count = 0
    for event in load_native_webhook_events(store, limit=limit):
        platform, client = str(event['platform']), str(event['client_id'])
        digest = str(event['event_id'])
        if client != native_setting(settings, platform, 'CLIENT_ID'):
            finish_native_webhook_event(store, digest, platform, client, processed=False, retry_after=30, error_code='application_configuration_changed')
            continue
        payload = _document(event['payload'])
        content = _document(payload.get('content'))
        candidate_ids = event['candidate_ids']
        if not candidate_ids:
            finish_native_webhook_event(store, digest, platform, client)
            continue
        targets = resolver(platform, candidate_ids)
        if not targets:
            finish_native_webhook_event(store, digest, platform, client, processed=False, retry_after=30, error_code='publication_checkpoint_pending')
            continue
        source_owner = str(payload.get('open_id') or payload.get('user_openid') or payload.get('from_user_id') or content.get('open_id') or content.get('openid') or '')
        external = str(content.get('share_id') or content.get('resource_id') or content.get('publish_id') or '')
        identity = str(content.get('video_id') or content.get('item_id') or content.get('resource_id') or '')
        queued = 0
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for persona, account_uid in set(targets):
                if source_owner and source_owner != account_uid and not account_uid.startswith(('appid:', 'app-handoff:')): continue
                normalized = {'platform': platform, 'account_uid': account_uid, 'external_request_id': external,
                    'platform_work_id': identity, 'candidate_ids': candidate_ids, 'event_type': payload.get('event') or payload.get('Event'),
                    'status': content.get('status') or payload.get('event'), 'permalink': content.get('share_url') or '',
                    'occurred_at': payload.get('create_time') or payload.get('CreateTime')}
                scoped = {**payload, '_completion_events': [normalized], '_verified_app_client_id': client}
                encrypted = store.cipher.encrypt(_DOCUMENT.dump_json(scoped))
                queued += db.execute('INSERT OR IGNORE INTO webhook_events VALUES(?,?,?,?,?,?)',
                    (digest, persona, platform, account_uid, event['received_at'], encrypted)).rowcount
            db.execute('UPDATE native_webhook_inbox SET processed=1,error_code=? WHERE digest=? AND platform=? AND client_id=?',
                ('' if queued else 'event_owner_mismatch_or_duplicate', digest, platform, client))
        count += queued
    return count
