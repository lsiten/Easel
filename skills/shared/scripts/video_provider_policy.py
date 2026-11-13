"""Provider, spend and provenance policy for video runs.

The policy is intentionally transport agnostic.  It produces serialisable
records for a run manifest and requires a pricing receipt before a paid build
can be started.  Secrets are referenced by identifier only and are never
accepted in a record.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


class ProviderPolicyError(ValueError):
    """Raised when a provider, credential or spend policy is invalid."""


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SECRET_NAMES = re.compile(r"(?i)(key|secret|token|password|credential|authorization)")
_ROUTES = frozenset({"local", "byok", "hypihub"})


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ProviderPolicyError(f"invalid {label}")
    return value


def _scope(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 120 or any(c in value for c in "/\\:\x00"):
        raise ProviderPolicyError(f"invalid {label}")
    return value


def _iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ProviderPolicyError("timestamp must include timezone")
        value = value.astimezone(timezone.utc).isoformat()
    if not isinstance(value, str):
        raise ProviderPolicyError("timestamp must be ISO text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ProviderPolicyError("invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderPolicyError("timestamp must include timezone")
    return value


def _redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, set):
        return {_redact_value(item) for item in value}
    if isinstance(value, str) and re.search(r"(?i)^(?:bearer\s+|(?:sk|pk|rk)-[A-Za-z0-9_-]{8,})", value):
        return "[REDACTED]"
    return value


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return metadata safe for logs and manifests."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if _SECRET_NAMES.search(str(key)):
            out[str(key)] = "[REDACTED]"
        else:
            out[str(key)] = _redact_value(value)
    return out


@dataclass(frozen=True)
class CredentialRef:
    credential_id: str
    profile_id: str
    session_id: str
    secret_env: str
    source: str = "environment"

    def __post_init__(self) -> None:
        _id(self.credential_id, "credential_id")
        _scope(self.profile_id, "profile_id")
        _scope(self.session_id, "session_id")
        if not isinstance(self.secret_env, str) or not self.secret_env or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", self.secret_env):
            raise ProviderPolicyError("invalid secret_env")
        if self.source not in {"environment", "keychain", "local-agent"}:
            raise ProviderPolicyError("unsupported credential source")

    def to_dict(self) -> dict[str, str]:
        return {"credential_id": self.credential_id, "profile_id": self.profile_id,
                "session_id": self.session_id, "secret_env": self.secret_env, "source": self.source}


@dataclass(frozen=True)
class ProviderRef:
    provider_id: str
    model_id: str
    endpoint: str
    profile_id: str
    session_id: str
    route: str = "local"
    credential_id: str | None = None
    adapter_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _id(self.provider_id, "provider_id")
        _id(self.model_id, "model_id")
        _scope(self.profile_id, "profile_id")
        _scope(self.session_id, "session_id")
        if self.route not in _ROUTES:
            raise ProviderPolicyError("unsupported provider route")
        if not isinstance(self.endpoint, str) or not self.endpoint or any(ch in self.endpoint for ch in "\r\n"):
            raise ProviderPolicyError("invalid endpoint")
        if self.route == "local" and self.credential_id is not None:
            raise ProviderPolicyError("local providers cannot bind a credential")
        if self.route != "local" and not self.credential_id:
            raise ProviderPolicyError("BYOK/HypiHub providers require a credential reference")
        if not isinstance(self.adapter_metadata, dict):
            raise ProviderPolicyError("adapter_metadata must be an object")

    def to_dict(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "model_id": self.model_id, "endpoint": self.endpoint,
                "profile_id": self.profile_id, "session_id": self.session_id, "route": self.route,
                "credential_id": self.credential_id,
                "adapter_metadata": redact_mapping(self.adapter_metadata)}


@dataclass(frozen=True)
class PricingEstimate:
    pricing_id: str
    provider_id: str
    profile_id: str
    session_id: str
    estimated_units: float
    estimated_cost: float
    currency: str = "USD"
    valid_until: str = ""

    def __post_init__(self) -> None:
        _id(self.pricing_id, "pricing_id")
        _id(self.provider_id, "provider_id")
        _scope(self.profile_id, "profile_id")
        _scope(self.session_id, "session_id")
        if (not isinstance(self.estimated_units, (int, float))
                or not isinstance(self.estimated_cost, (int, float))
                or not math.isfinite(self.estimated_units)
                or not math.isfinite(self.estimated_cost)
                or self.estimated_units < 0 or self.estimated_cost < 0 or not self.currency):
            raise ProviderPolicyError("pricing values must be non-negative")
        _iso(self.valid_until)

    def to_dict(self) -> dict[str, Any]:
        return {"pricing_id": self.pricing_id, "provider_id": self.provider_id,
                "profile_id": self.profile_id, "session_id": self.session_id,
                "estimated_units": self.estimated_units, "estimated_cost": self.estimated_cost,
                "currency": self.currency, "valid_until": self.valid_until}


@dataclass(frozen=True)
class ApprovalReceipt:
    approval_id: str
    pricing_id: str
    profile_id: str
    session_id: str
    approved_at: str
    approved_by: str
    scope: str = "build"

    def __post_init__(self) -> None:
        for value, label in ((self.approval_id, "approval_id"), (self.pricing_id, "pricing_id"), (self.approved_by, "approved_by")):
            _id(value, label)
        _scope(self.profile_id, "profile_id")
        _scope(self.session_id, "session_id")
        if self.scope not in {"sample", "build"}:
            raise ProviderPolicyError("invalid approval scope")
        _iso(self.approved_at)

    def to_dict(self) -> dict[str, str]:
        return {"approval_id": self.approval_id, "pricing_id": self.pricing_id,
                "profile_id": self.profile_id, "session_id": self.session_id,
                "approved_at": self.approved_at, "approved_by": self.approved_by, "scope": self.scope}


@dataclass(frozen=True)
class ProvenanceRecord:
    source_id: str
    source_uri: str
    rights: str
    attribution: str = ""
    license: str = ""
    sha256: str = ""

    def __post_init__(self) -> None:
        _id(self.source_id, "source_id")
        if not self.source_uri or any(ch in self.source_uri for ch in "\r\n"):
            raise ProviderPolicyError("invalid source_uri")
        if not self.rights:
            raise ProviderPolicyError("rights are required")
        if self.sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ProviderPolicyError("invalid sha256")

    def to_dict(self) -> dict[str, str]:
        return {"source_id": self.source_id, "source_uri": self.source_uri, "rights": self.rights,
                "attribution": self.attribution, "license": self.license, "sha256": self.sha256}


@dataclass(frozen=True)
class ProviderPolicy:
    provider: ProviderRef
    credential: CredentialRef | None = None
    pricing: PricingEstimate | None = None
    approval: ApprovalReceipt | None = None
    provenance: tuple[ProvenanceRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.provider.route != "local" and self.credential is None:
            raise ProviderPolicyError("credential reference is required")
        if self.credential and self.credential.credential_id != self.provider.credential_id:
            raise ProviderPolicyError("credential does not match provider credential")
        if self.credential and (self.credential.profile_id != self.provider.profile_id
                                or self.credential.session_id != self.provider.session_id):
            raise ProviderPolicyError("credential is outside provider profile/session")
        if self.pricing and (self.pricing.provider_id != self.provider.provider_id or self.pricing.profile_id != self.provider.profile_id or self.pricing.session_id != self.provider.session_id):
            raise ProviderPolicyError("pricing is outside provider scope")
        if self.approval and (self.pricing is None or self.approval.pricing_id != self.pricing.pricing_id or self.approval.profile_id != self.provider.profile_id or self.approval.session_id != self.provider.session_id):
            raise ProviderPolicyError("approval does not match pricing/provider scope")
        if self.provider.route == "hypihub" and self.approval is None:
            # HypiHub is always opt-in and cannot become active through metadata alone.
            return

    @property
    def ready(self) -> bool:
        return self.provider.route == "local" or self.credential is not None

    def assert_build_allowed(self, *, now: datetime | None = None, paid: bool = True,
                             build_kind: str = "full") -> None:
        if build_kind not in {"sample", "full"}:
            raise ProviderPolicyError("invalid build kind")
        if not self.ready:
            raise ProviderPolicyError("provider is not ready")
        if not paid or self.provider.route == "local":
            return
        if self.pricing is None:
            raise ProviderPolicyError("pricing is required before Build")
        current_value = now or datetime.now(timezone.utc)
        if current_value.tzinfo is None or current_value.utcoffset() is None:
            raise ProviderPolicyError("timestamp must include timezone")
        current = current_value.astimezone(timezone.utc)
        try:
            expiry = datetime.fromisoformat(self.pricing.valid_until.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ProviderPolicyError("invalid pricing timestamp") from exc
        if expiry.tzinfo is None or expiry.utcoffset() is None:
            raise ProviderPolicyError("pricing timestamp must include timezone")
        expiry = expiry.astimezone(timezone.utc)
        if expiry <= current:
            raise ProviderPolicyError("pricing estimate is stale")
        if self.approval is None:
            raise ProviderPolicyError("explicit user approval is required before paid Build")
        if build_kind == "full" and self.approval.scope != "build":
            raise ProviderPolicyError("approval scope does not cover full Build")
        if build_kind == "sample" and self.approval.scope not in {"sample", "build"}:
            raise ProviderPolicyError("approval scope does not cover sample Build")

    def to_manifest_dict(self) -> dict[str, Any]:
        return {"provider": self.provider.to_dict(),
                "credential": self.credential.to_dict() if self.credential else None,
                "pricing": self.pricing.to_dict() if self.pricing else None,
                "approval": self.approval.to_dict() if self.approval else None,
                "provenance": [item.to_dict() for item in self.provenance]}


def pricing_for(provider: ProviderRef, *, estimated_units: float, unit_cost: float,
                valid_until: datetime | str, pricing_id: str = "pricing-1") -> PricingEstimate:
    return PricingEstimate(pricing_id=pricing_id, provider_id=provider.provider_id,
                           profile_id=provider.profile_id, session_id=provider.session_id,
                           estimated_units=estimated_units, estimated_cost=estimated_units * unit_cost,
                           valid_until=_iso(valid_until))


def provenance_for(source_id: str, source_uri: str, rights: str, *, content: bytes | None = None, **kwargs: str) -> ProvenanceRecord:
    digest = hashlib.sha256(content).hexdigest() if content is not None else ""
    return ProvenanceRecord(source_id=source_id, source_uri=source_uri, rights=rights, sha256=digest, **kwargs)


def manifest_policy_json(policy: ProviderPolicy) -> str:
    return json.dumps(policy.to_manifest_dict(), ensure_ascii=False, sort_keys=True)
