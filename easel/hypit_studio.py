"""Scoped Hypit Studio embedding credentials and origin policy."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.parse
from typing import Any, Mapping

AUDIENCE = "hypit-studio"
ENGINE = "hypit"
SOURCE = "easel"
MAX_TTL_SECONDS = 300
_DEFAULT_SECRET = secrets.token_bytes(32)
_USED: dict[str, float] = {}
_LOCK = threading.Lock()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def allowed_origins() -> tuple[str, ...]:
    """Return exact, scheme-qualified origins configured for Studio embedding."""
    raw = os.environ.get("EASEL_HYPIT_STUDIO_ORIGINS", "")
    origins: list[str] = []
    for item in raw.split(","):
        origin = item.strip().rstrip("/")
        if not origin or origin == "*":
            continue
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            continue
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


def studio_url() -> str:
    value = os.environ.get("EASEL_HYPIT_STUDIO_URL", "").strip()
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return value


def studio_origin(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""


def license_available() -> bool:
    value = os.environ.get("EASEL_HYPIT_STUDIO_LICENSE", "enabled").strip().lower()
    return value not in {"0", "false", "no", "disabled", "missing", "unlicensed"}


def token_secret() -> bytes:
    value = os.environ.get("EASEL_HYPIT_STUDIO_SECRET", "").encode("utf-8")
    return value or _DEFAULT_SECRET


def issue_token(*, run_id: str, profile_id: str, session_id: str, ttl: int = 300, now: float | None = None) -> dict[str, Any]:
    current = int(time.time() if now is None else now)
    lifetime = max(1, min(int(ttl), MAX_TTL_SECONDS))
    claims: dict[str, Any] = {
        "run": run_id,
        "profile": profile_id,
        "session": session_id,
        "run_id": run_id,
        "profile_id": profile_id,
        "session_id": session_id,
        "aud": AUDIENCE,
        "engine": ENGINE,
        "source": SOURCE,
        "iat": current,
        "exp": current + lifetime,
        "jti": secrets.token_urlsafe(18),
        "nonce": secrets.token_urlsafe(18),
    }
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    encoded = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{header}.{encoded}"
    signature = _b64(hmac.new(token_secret(), signing_input.encode("ascii"), hashlib.sha256).digest())
    return {"token": f"{signing_input}.{signature}", "claims": claims}


def validate_token(token: str, *, run_id: str, profile_id: str, session_id: str,
                   nonce: str | None = None, now: float | None = None, consume: bool = True) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) == 3:
            header, encoded, signature = parts
            parsed_header = json.loads(_unb64(header).decode("utf-8"))
            if parsed_header.get("alg") != "HS256" or parsed_header.get("typ") != "JWT":
                raise ValueError("invalid token header")
            signing_input = f"{header}.{encoded}"
        elif len(parts) == 2:
            encoded, signature = parts
            signing_input = encoded
        else:
            raise ValueError("malformed studio token")
        expected = _b64(hmac.new(token_secret(), signing_input.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        claims = json.loads(_unb64(encoded).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, base64.binascii.Error) as exc:
        raise ValueError("malformed studio token") from exc
    current = int(time.time() if now is None else now)
    if claims.get("aud") != AUDIENCE or claims.get("engine") != ENGINE or claims.get("source") != SOURCE:
        raise ValueError("studio token identity mismatch")
    if not isinstance(claims.get("exp"), int) or claims["exp"] <= current:
        raise ValueError("studio token expired")
    if claims["exp"] - int(claims.get("iat", current)) > MAX_TTL_SECONDS:
        raise ValueError("studio token lifetime exceeds limit")
    if (claims.get("run_id"), claims.get("profile_id"), claims.get("session_id")) != (run_id, profile_id, session_id):
        raise ValueError("studio token scope mismatch")
    if nonce is not None and nonce != claims.get("nonce"):
        raise ValueError("studio token nonce mismatch")
    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti:
        raise ValueError("studio token missing jti")
    with _LOCK:
        for key, expiry in list(_USED.items()):
            if expiry <= current:
                _USED.pop(key, None)
        if consume and jti in _USED:
            raise ValueError("studio token replayed")
        if consume:
            _USED[jti] = claims["exp"]
    return claims


def embed_url(base: str, token: str, run_id: str) -> str:
    parsed = urllib.parse.urlsplit(base)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query["easel_token"] = [token]
    query["run_id"] = [run_id]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query, doseq=True), parsed.fragment))


def frame_ancestors() -> str:
    origins = allowed_origins()
    return "frame-ancestors " + (" ".join(origins) if origins else "'none'")


def policy_for_url(url: str) -> bool:
    origin = studio_origin(url)
    return bool(origin and origin in allowed_origins())


__all__ = ["AUDIENCE", "ENGINE", "SOURCE", "MAX_TTL_SECONDS", "allowed_origins", "studio_url", "studio_origin", "license_available", "issue_token", "validate_token", "embed_url", "frame_ancestors", "policy_for_url"]
