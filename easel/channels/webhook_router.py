"""Verified webhook inbox, scoped to already-connected persona accounts."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import time
import xml.etree.ElementTree as ET

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import TypeAdapter, ValidationError

from easel.channels.models import ChannelError, Credential, JsonDocument
from easel.channels.store import CredentialStore
from easel.channels.webhooks import verify_webhook, webhook_challenge
from easel.channels.completion_events import delivery_schema, normalize_completion_events, scope_event_payload
from easel.channels.native_webhooks import NATIVE_WEBHOOKS, verify_native, save_native_event, native_event

_DOCUMENT = TypeAdapter(JsonDocument)
_SUPPORTED = {'youtube', 'tiktok', 'instagram', 'facebook', 'linkedin'} | NATIVE_WEBHOOKS


def parse_event(platform: str, body: bytes) -> tuple[set[str], JsonDocument]:
    if platform == 'youtube':
        if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
            raise ChannelError(platform, 'webhook_body_invalid')
        try:
            root = ET.fromstring(body)
        except ET.ParseError as error:
            raise ChannelError(platform, 'webhook_body_invalid') from error
        channels = {element.text for element in root.iter() if element.tag.endswith('}channelId') and element.text}
        videos = [element.text for element in root.iter() if element.tag.endswith('}videoId') and element.text]
        entries = []
        for entry in root.iter():
            if not entry.tag.endswith('}entry'): continue
            fields = {element.tag.rsplit('}', 1)[-1]: element.text for element in entry.iter()}
            if fields.get('channelId') and fields.get('videoId'):
                entries.append({'channel_id': fields['channelId'], 'video_id': fields['videoId']})
        return channels, {'channel_ids': sorted(channels), 'video_ids': videos, 'entries': entries}
    try:
        payload = _DOCUMENT.validate_json(body)
    except ValidationError as error:
        raise ChannelError(platform, 'webhook_body_invalid') from error
    if platform in {'facebook', 'instagram'}:
        entries = payload.get('entry')
        subjects = {str(row['id']) for row in entries if isinstance(row, dict) and row.get('id')} if isinstance(entries, list) else set()
    elif platform == 'tiktok':
        subjects = {str(payload.get('user_openid') or '')} - {''}
    else:
        subjects = {str(payload.get('owner') or '')} - {''}
        subjects |= {value.removeprefix('urn:li:person:') for value in subjects}
    return subjects, payload


def _schema(store: CredentialStore) -> None:
    with store.connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS webhook_events(
            digest TEXT NOT NULL, persona TEXT NOT NULL, platform TEXT NOT NULL, account_uid TEXT NOT NULL,
            received_at REAL NOT NULL, encrypted BLOB NOT NULL,
            PRIMARY KEY(digest,persona,platform,account_uid))''')


def add_webhook_routes(router: APIRouter, store_factory: Callable[[], CredentialStore], settings: Callable[[], Mapping[str, str]],
                       credential: Callable[[str, str, str], Credential]) -> None:
    @router.get('/{platform}/webhook')
    def challenge(platform: str, request: Request):
        if platform not in _SUPPORTED: raise HTTPException(405, 'webhook unsupported')
        prefix = 'EASEL_' + platform.upper().replace('-', '_') + '_'
        values = settings()
        if platform in NATIVE_WEBHOOKS:
            try:
                valid = platform == 'wechat-official-open' and verify_native(platform, b'', request.headers, dict(request.query_params), values)
            except ChannelError:
                valid = False
            if not valid: raise HTTPException(403, 'webhook signature invalid')
            return PlainTextResponse(request.query_params.get('echostr', ''))
        try:
            result = webhook_challenge(platform, dict(request.query_params), values.get(prefix + 'WEBHOOK_VERIFY_TOKEN', ''),
                                       values.get(prefix + 'WEBHOOK_SECRET') or values.get(prefix + 'CLIENT_SECRET', ''))
        except ChannelError as error:
            raise HTTPException(error.status, error.code) from error
        return result if platform == 'linkedin' else PlainTextResponse(str(result.get('challenge') or ''))

    @router.post('/{platform}/webhook', status_code=202)
    async def incoming(platform: str, request: Request):
        if platform not in _SUPPORTED: raise HTTPException(405, 'webhook unsupported')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024: raise HTTPException(413, 'webhook body too large')
        prefix = 'EASEL_' + platform.upper().replace('-', '_') + '_'
        values = settings()
        if platform in NATIVE_WEBHOOKS:
            try:
                if not verify_native(platform, bytes(body), request.headers, dict(request.query_params), values): raise HTTPException(403, 'webhook signature invalid')
                payload, _ = native_event(platform, bytes(body))
                event = payload.get('event')
                content = payload.get('content') if isinstance(payload.get('content'), dict) else {}
                if platform == 'douyin-open' and event == 'verify_webhook': return JSONResponse({'challenge': content.get('challenge')})
                if platform == 'bilibili-open' and event == 'verify_webhooks': return JSONResponse({'data': content.get('data')})
                save_native_event(store_factory(), platform, bytes(body), values)
                return PlainTextResponse('success') if platform == 'wechat-official-open' else JSONResponse({'code': 0, 'message': 'ok'})
            except ChannelError as error:
                raise HTTPException(error.status, error.code) from error
        secret = values.get(prefix + 'WEBHOOK_SECRET') or values.get(prefix + 'CLIENT_SECRET', '')
        if not verify_webhook(platform, bytes(body), request.headers, secret): raise HTTPException(403, 'webhook signature invalid')
        try:
            subjects, payload = parse_event(platform, bytes(body))
        except ChannelError as error:
            raise HTTPException(error.status, error.code) from error
        payload['_completion_events'] = normalize_completion_events(platform, payload)
        store = store_factory()
        _schema(store)
        digest = hashlib.sha256(bytes(body)).hexdigest()
        count = 0
        with store.connection() as db:
            rows = db.execute('SELECT persona,account_uid FROM credentials WHERE platform=?', (platform,)).fetchall()
            for row in rows:
                if row['account_uid'] in subjects:
                    scoped = scope_event_payload(platform, payload, row['account_uid'])
                    encrypted = store.cipher.encrypt(_DOCUMENT.dump_json(scoped))
                    count += db.execute('INSERT OR IGNORE INTO webhook_events VALUES(?,?,?,?,?,?)',
                        (digest, row['persona'], platform, row['account_uid'], time.time(), encrypted)).rowcount
        return {'accepted': True, 'queued_events': count}

    @router.get('/{platform}/webhook-events')
    def events(platform: str, persona: str, account_uid: str):
        try:
            account = credential(platform, persona, account_uid)
            store = store_factory()
            _schema(store)
            delivery_schema(store)
            with store.connection() as db:
                rows = db.execute('''SELECT e.digest,e.received_at,e.encrypted,d.processed,d.next_attempt,d.error_code
                    FROM webhook_events e LEFT JOIN webhook_deliveries d ON(e.digest=d.digest AND e.persona=d.persona AND e.platform=d.platform AND e.account_uid=d.account_uid)
                    WHERE e.persona=? AND e.platform=? AND e.account_uid=? ORDER BY e.received_at DESC LIMIT 100''',
                    (account.persona, account.platform, account.account_uid)).fetchall()
            return [{'event_id': row['digest'], 'received_at': row['received_at'],
                     'delivery': {'processed': bool(row['processed']), 'next_attempt': row['next_attempt'] or 0, 'error_code': row['error_code'] or ''},
                     'payload': _DOCUMENT.validate_json(store.cipher.decrypt(row['encrypted']))} for row in rows]
        except ChannelError as error:
            raise HTTPException(error.status, error.code) from error
