"""Source-compatible Rednote SDK signing through an operator-configured token bridge."""
from collections.abc import Mapping
import hashlib
import json
import secrets
import time
from urllib.parse import urlsplit

import httpx

from easel.channels.models import ChannelError, JsonDocument
from easel.channels.native_config import native_setting


def _signature(app_key: str, nonce: str, timestamp: str, secret: str) -> str:
    material = f'appKey={app_key}&nonce={nonce}&timeStamp={timestamp}' + secret
    return hashlib.sha256(material.encode()).hexdigest()


def share_config(settings: Mapping[str, str], nonce: str = '', *, transport: httpx.BaseTransport | None = None) -> JsonDocument:
    key = native_setting(settings, 'rednote-link', 'CLIENT_ID')
    secret = native_setting(settings, 'rednote-link', 'CLIENT_SECRET')
    endpoint = native_setting(settings, 'rednote-link', 'ACCESS_TOKEN_URL')
    if not key or not secret or not endpoint:
        raise ChannelError('rednote-link', 'share_application_not_configured', status=409)
    try:
        parsed = urlsplit(endpoint)
    except ValueError as error:
        raise ChannelError('rednote-link', 'token_bridge_url_invalid') from error
    local = parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}
    if (parsed.scheme != 'https' and not local) or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ChannelError('rednote-link', 'token_bridge_url_invalid')
    nonce = nonce.strip() or secrets.token_urlsafe(12)
    if not 1 <= len(nonce) <= 64:
        raise ChannelError('rednote-link', 'nonce_invalid')
    timestamp = str(time.time_ns() // 1_000_000)
    body = {'app_key': key, 'nonce': nonce, 'timestamp': timestamp,
            'signature': _signature(key, nonce, timestamp, secret)}
    try:
        with httpx.Client(transport=transport, trust_env=False, follow_redirects=False, timeout=10) as client:
            with client.stream('POST', endpoint, json=body) as response:
                response.raise_for_status()
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 64 * 1024:
                        raise ChannelError('rednote-link', 'token_bridge_response_invalid', status=502)
                value = json.loads(payload)
    except (httpx.HTTPError, ValueError) as error:
        raise ChannelError('rednote-link', 'token_bridge_unavailable', status=502) from error
    data = value.get('data') if isinstance(value, dict) else None
    token = data.get('access_token') if isinstance(data, dict) else None
    if not isinstance(value, dict) or value.get('success') is not True or not isinstance(token, str) or not token or len(token) > 8192:
        raise ChannelError('rednote-link', 'token_bridge_response_invalid', status=502)
    return {'verifyConfig': {'appKey': key, 'nonce': nonce, 'timestamp': timestamp,
            'signature': _signature(key, nonce, timestamp, token)}}
