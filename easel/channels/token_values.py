"""Validate token lifetimes and refreshed identities before saving a credential."""
import math
import time

from easel.channels.models import ChannelError, Credential, JsonDocument


def token_expiry(platform: str, payload: JsonDocument) -> float | None:
    value = payload.get('expires_in')
    if value is None: return None
    return time.time() + token_seconds(platform, value)


def token_seconds(platform: str, value, default: float = 7200) -> float:
    if value is None: return default
    if type(value) not in (str, int, float): raise ChannelError(platform, 'token_expiry_invalid', status=502)
    try:
        seconds = float(value)
    except ValueError as error:
        raise ChannelError(platform, 'token_expiry_invalid', status=502) from error
    if not math.isfinite(seconds) or seconds <= 0: raise ChannelError(platform, 'token_expiry_invalid', status=502)
    return seconds


def token_identity(platform: str, payload: JsonDocument, previous: Credential | None) -> str:
    supplied = payload.get('open_id') or payload.get('user_id')
    if previous and supplied and str(supplied) not in {previous.account_uid, previous.parent_uid}:
        raise ChannelError(platform, 'refreshed_identity_mismatch', status=403)
    if platform == 'wechat-official-open' and previous: return previous.account_uid
    return str(supplied or (previous.account_uid if previous else 'pending'))
