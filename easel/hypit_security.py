"""Security and operations controls for local Hypit video replication.

The module is intentionally provider-neutral.  It keeps reference fetching,
media limits, retention and license checks opt-in to Hypit callers.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

MAX_REDIRECTS = 3
MAX_BYTES = 1_000_000_000
MAX_DURATION_SECONDS = 20 * 60
MAX_WIDTH = 1920
MAX_HEIGHT = 1080
RETENTION_BYTES = 10 * 1_000_000_000
RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_CONCURRENT_SESSION = 1
MAX_CONCURRENT_PROFILE = 2
MAX_TIMEOUT_SECONDS = 2 * 60 * 60

class SecurityError(ValueError):
    pass
class ReferenceURLBlocked(SecurityError):
    pass
class MediaLimitExceeded(SecurityError):
    pass
class RetentionError(SecurityError):
    pass
class LicenseError(SecurityError):
    pass


def reference_hosts() -> tuple[str, ...]:
    """Exact hosts permitted for remote references. Empty by default."""
    raw = os.environ.get("EASEL_VIDEO_REFERENCE_HOSTS", "")
    return tuple(dict.fromkeys(x.strip().lower().rstrip(".") for x in raw.split(",") if x.strip()))


def _resolved_addresses(host: str, resolver: Callable[..., Any] | None = None) -> set[ipaddress._BaseAddress]:
    if resolver is None:
        resolver = socket.getaddrinfo
    try:
        rows = resolver(host, None, type=socket.SOCK_STREAM)
    except TypeError:
        rows = resolver(host, None)
    except OSError as exc:
        raise ReferenceURLBlocked("reference host cannot be resolved") from exc
    addresses: set[ipaddress._BaseAddress] = set()
    for row in rows:
        value = row[4][0] if isinstance(row, tuple) and len(row) > 4 else row
        try:
            addresses.add(ipaddress.ip_address(value))
        except ValueError:
            continue
    if not addresses:
        raise ReferenceURLBlocked("reference host cannot be resolved")
    return addresses


def _blocked_address(value: ipaddress._BaseAddress) -> bool:
    return bool(value.is_private or value.is_loopback or value.is_link_local or value.is_multicast or value.is_unspecified or value.is_reserved)


def validate_reference_url(url: str, *, resolver: Callable[..., Any] | None = None, hosts: Iterable[str] | None = None) -> str:
    """Validate one URL before every request and redirect.

    Credentials, non-http schemes, unallowlisted hosts and private/link-local/
    multicast/reserved addresses are rejected. DNS is resolved on each call,
    allowing callers to revalidate redirected destinations and prevent rebinding.
    """
    if not isinstance(url, str) or len(url) > 4096:
        raise ReferenceURLBlocked("invalid reference URL")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ReferenceURLBlocked("reference URL scheme or credentials blocked")
    allowed = tuple(x.lower().rstrip(".") for x in (reference_hosts() if hosts is None else hosts))
    hostname = parsed.hostname.lower().rstrip(".")
    if not allowed or hostname not in allowed:
        raise ReferenceURLBlocked("reference host is not allowlisted")
    for address in _resolved_addresses(hostname, resolver):
        if _blocked_address(address):
            raise ReferenceURLBlocked("reference address is private or special-use")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def fetch_reference(url: str, *, opener: Callable[..., Any] | None = None, resolver: Callable[..., Any] | None = None,
                    max_bytes: int = MAX_BYTES, max_redirects: int = MAX_REDIRECTS) -> bytes:
    """Download a reference with bounded redirects and streaming byte cap."""
    if max_bytes <= 0 or max_redirects < 0:
        raise ValueError("invalid fetch limits")
    current = validate_reference_url(url, resolver=resolver)
    if opener is None:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        open_fn = urllib.request.build_opener(_NoRedirect()).open
    else:
        open_fn = opener
    for _ in range(max_redirects + 1):
        response = open_fn(current, timeout=30)
        try:
            status = getattr(response, "status", 200)
            location = response.headers.get("Location") if hasattr(response, "headers") else None
            if status in {301, 302, 303, 307, 308} and location:
                current = validate_reference_url(urllib.parse.urljoin(current, location), resolver=resolver)
                continue
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise MediaLimitExceeded("reference byte cap exceeded")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
    raise ReferenceURLBlocked("too many redirects")


def validate_video_metadata(*, byte_size: int, duration_seconds: float, width: int, height: int,
                            decompressed_bytes: int | None = None) -> None:
    """Enforce the 1GB, 20-minute, 1080p and decompression limits."""
    if byte_size < 0 or byte_size > MAX_BYTES:
        raise MediaLimitExceeded("video byte cap exceeded")
    if decompressed_bytes is not None and (decompressed_bytes < 0 or decompressed_bytes > MAX_BYTES):
        raise MediaLimitExceeded("decompressed byte cap exceeded")
    if duration_seconds < 0 or duration_seconds > MAX_DURATION_SECONDS:
        raise MediaLimitExceeded("video duration cap exceeded")
    if width <= 0 or height <= 0 or width > MAX_WIDTH or height > MAX_HEIGHT:
        raise MediaLimitExceeded("video resolution cap exceeded")


def redact(value: Any) -> Any:
    """Recursively remove credentials, cookies, absolute paths and query secrets."""
    secret = re.compile(r"(?i)(bearer\s+|(?:client[_ -]?secret|api[_ -]?key|secret|token|password|cookie|credential)\s*[=:]\s*)[^\s,;]+")
    path = re.compile(r"(?:/Users/|/home/|/tmp/|[A-Za-z]:\\)[^\s,;]+")
    if isinstance(value, str):
        return path.sub("[PATH_REDACTED]", secret.sub("[REDACTED]", value))
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key).lower()
            if any(word in name for word in ("secret", "token", "password", "credential", "cookie", "api_key")):
                out[str(key)] = "[REDACTED]"
            else:
                out[str(key)] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


@dataclass(frozen=True)
class RetentionItem:
    path: str
    size: int
    created_at: float
    active: bool = False
    shared: bool = False
    approved: bool = False
    audit_pinned: bool = False


def cleanup_retention(root: Path | str, *, now: float | None = None, max_bytes: int = RETENTION_BYTES,
                      max_age_seconds: int = RETENTION_SECONDS, items: Iterable[RetentionItem] | None = None) -> list[str]:
    """Delete expired/unpinned artifacts until age and aggregate limits hold."""
    base = Path(root)
    current = time.time() if now is None else now
    records = list(items) if items is not None else [RetentionItem(str(p), p.stat().st_size, p.stat().st_mtime) for p in base.rglob("*") if p.is_file()]
    removable = [x for x in records if not (x.active or x.shared or x.approved or x.audit_pinned)]
    removed: list[str] = []
    total = sum(max(0, x.size) for x in records)
    for item in sorted(removable, key=lambda x: x.created_at):
        if current - item.created_at <= max_age_seconds and total <= max_bytes:
            continue
        try:
            Path(item.path).unlink(missing_ok=True)
        except OSError:
            continue
        total -= max(0, item.size)
        removed.append(item.path)
    return removed


def migration_with_rollback(root: Path | str, migrate: Callable[[Path], None], *, marker: str = "migration.json") -> None:
    """Run a migration transactionally, restoring the fixture on failure."""
    base = Path(root)
    backup = Path(tempfile.mkdtemp(prefix="hypit-migration-"))
    try:
        if base.exists():
            shutil.copytree(base, backup / "snapshot")
        try:
            migrate(base)
        except Exception:
            if base.exists():
                shutil.rmtree(base)
            if (backup / "snapshot").exists():
                shutil.copytree(backup / "snapshot", base)
            raise
        base.mkdir(parents=True, exist_ok=True)
        (base / marker).write_text(json.dumps({"schema": 1, "migrated_at": time.time()}) + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(backup, ignore_errors=True)


def license_gate(*, attribution: str | None = None, commercial: bool = False, multi_tenant: bool = False) -> dict[str, Any]:
    """Allow local/single-org use only and require attribution for shared output."""
    if commercial or multi_tenant:
        raise LicenseError("Hypit replication is limited to local or single-organization use")
    value = os.environ.get("EASEL_HYPIT_STUDIO_LICENSE", "enabled").strip().lower()
    if value in {"0", "false", "no", "disabled", "missing", "unlicensed"}:
        raise LicenseError("Hypit license unavailable")
    required = os.environ.get("EASEL_HYPIT_ATTRIBUTION", "Hypit replication powered by Easel").strip()
    if not attribution or attribution.strip() != required:
        raise LicenseError("required attribution missing")
    return {"licensed": True, "scope": "local-single-org", "attribution": required}

__all__ = ["MAX_REDIRECTS", "MAX_BYTES", "MAX_DURATION_SECONDS", "MAX_WIDTH", "MAX_HEIGHT", "RETENTION_BYTES", "RETENTION_SECONDS", "MAX_CONCURRENT_SESSION", "MAX_CONCURRENT_PROFILE", "MAX_TIMEOUT_SECONDS", "SecurityError", "ReferenceURLBlocked", "MediaLimitExceeded", "RetentionError", "LicenseError", "reference_hosts", "validate_reference_url", "fetch_reference", "validate_video_metadata", "redact", "RetentionItem", "cleanup_retention", "migration_with_rollback", "license_gate"]

# Compatibility spellings used by integrations.
validate_url = validate_reference_url
stream_download = fetch_reference
enforce_media_caps = validate_video_metadata
redact_log = redact
cleanup_orphans = cleanup_retention
check_license = license_gate
