"""Webhook authenticity checks without a domain-state mutation side door."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import time

from easel.channels.models import ChannelError, JsonDocument, PlatformId


def verify_webhook(platform: PlatformId, body: bytes, headers: Mapping[str, str], secret: str, *, now: float | None = None) -> bool:
    if not secret or len(body) > 1024 * 1024:
        return False
    values = {key.lower(): value for key, value in headers.items()}
    match platform:
        case 'facebook' | 'instagram':
            supplied = values.get('x-hub-signature-256', '')
            expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        case 'linkedin':
            supplied = values.get('x-li-signature', '')
            digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            expected = ('sha256=' if supplied.startswith('sha256=') else '') + digest
        case 'tiktok':
            supplied_header = values.get('tiktok-signature') or values.get('x-tiktok-signature') or values.get('x-tt-signature') or ''
            parts = dict(part.strip().split('=', 1) for part in supplied_header.split(',') if '=' in part)
            stamp = parts.get('t', '')
            if not stamp.isdecimal() or abs((time.time() if now is None else now) - int(stamp)) > 300:
                return False
            supplied = parts.get('s', '')
            expected = hmac.new(secret.encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
        case 'youtube':
            supplied = values.get('x-hub-signature', '')
            algorithm = 'sha256' if supplied.startswith('sha256=') else 'sha1'
            expected = algorithm + '=' + hmac.new(secret.encode(), body, algorithm).hexdigest()
        case _:
            return False
    return bool(supplied) and hmac.compare_digest(supplied.encode(), expected.encode())


def webhook_challenge(platform: PlatformId, query: Mapping[str, str], verify_token: str, secret: str = '') -> JsonDocument:
    match platform:
        case 'facebook' | 'instagram' | 'youtube':
            if not verify_token or not hmac.compare_digest(query.get('hub.verify_token', '').encode(), verify_token.encode()):
                raise ChannelError(platform, 'webhook_verification_failed', status=403)
            if query.get('hub.mode') not in {'subscribe', 'unsubscribe'} or not query.get('hub.challenge'):
                raise ChannelError(platform, 'webhook_challenge_invalid')
            return {'challenge': query['hub.challenge']}
        case 'linkedin':
            challenge = query.get('challengeCode', '')
            if not challenge or not secret: raise ChannelError(platform, 'webhook_challenge_invalid')
            return {'challengeCode': challenge, 'challengeResponse': hmac.new(secret.encode(), challenge.encode(), hashlib.sha256).hexdigest()}
        case 'tiktok':
            return {'challenge': query.get('challenge', '')}
        case _:
            raise ChannelError(platform, 'webhook_unsupported', status=405)
