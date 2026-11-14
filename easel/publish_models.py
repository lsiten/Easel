"""Publishing state, events, and content identities."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Mapping, Final, Union, List, Dict, Tuple

JsonValue = Union[None, bool, int, float, str, List["JsonValue"], Dict[str, "JsonValue"], Tuple["JsonValue", ...]]

class LedgerError(RuntimeError):
    """Base error for ledger failures."""


class IdempotencyConflict(LedgerError):
    """An idempotency key was reused with different immutable inputs."""


class InvalidTransition(LedgerError):
    """A compare-and-set state transition was rejected."""


class LeaseBusy(LedgerError):
    """Another worker currently owns the run lease."""


class LeaseRequired(LedgerError):
    """A fenced transition was attempted without the current lease token."""


class RunState(str, Enum):
    PREPARED = "prepared"
    APPROVED = "approved"
    QUEUED = "queued"
    RUNNING = "running"
    READY_FOR_MANUAL_SUBMISSION = "ready_for_manual_submission"
    SUBMITTED = "submitted"
    PUBLISHED = "published"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILING = "reconciling"
    CANCELLED = "cancelled"


_ALLOWED: Final[dict[str, frozenset[str]]] = {
    RunState.PREPARED: frozenset({RunState.APPROVED, RunState.CANCELLED}),
    RunState.APPROVED: frozenset({RunState.QUEUED, RunState.READY_FOR_MANUAL_SUBMISSION, RunState.CANCELLED}),
    RunState.QUEUED: frozenset({RunState.RUNNING, RunState.CANCELLED}),
    RunState.RUNNING: frozenset({RunState.SUBMITTED, RunState.PUBLISHED, RunState.FAILED, RunState.UNKNOWN}),
    RunState.SUBMITTED: frozenset({RunState.PUBLISHED, RunState.FAILED, RunState.UNKNOWN, RunState.RECONCILING}),
    RunState.UNKNOWN: frozenset({RunState.RECONCILING, RunState.FAILED, RunState.PUBLISHED, RunState.CANCELLED}),
    RunState.RECONCILING: frozenset({RunState.PUBLISHED, RunState.FAILED, RunState.UNKNOWN}),
    RunState.READY_FOR_MANUAL_SUBMISSION: frozenset({RunState.SUBMITTED, RunState.PUBLISHED, RunState.UNKNOWN, RunState.CANCELLED}),
    RunState.FAILED: frozenset({RunState.RECONCILING, RunState.QUEUED, RunState.CANCELLED}),
    RunState.PUBLISHED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class PublishRun:
    run_id: str
    platform: str
    persona: str
    idempotency_key: str
    state: RunState
    approval_digest: str
    media_manifest_hash: str
    account_id: str
    created_at: str
    updated_at: str
    generation: int
    attempts: int
    last_error: str
    external_request_id: str
    platform_work_id: str
    permalink: str
    payload: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class PublishEvent:
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    from_state: str
    to_state: str
    actor: str
    payload: Mapping[str, JsonValue]
    created_at: str


@dataclass(frozen=True, slots=True)
class PublishLease:
    run_id: str
    owner: str
    fencing_token: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class PublishEffect:
    effect_key: str
    run_id: str
    effect_type: str
    status: str
    payload: Mapping[str, JsonValue]
    created_at: str
    completed_at: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Mapping[str, JsonValue]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_mapping(value: Mapping[str, JsonValue]) -> str:
    """Return a stable digest for approval or content inputs."""
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def content_identity(payload: Mapping[str, JsonValue]) -> str:
    content = payload.get('channel_intent')
    document = content if isinstance(content, dict) else payload
    return digest_mapping({key: document.get(key, '') for key in ('title', 'body', 'tags', 'options')})
