"""Public import for the shared video provider policy contract."""
from pathlib import Path
import importlib.util
import sys

_SHARED = Path(__file__).resolve().parents[1] / "skills" / "shared" / "scripts" / "video_provider_policy.py"
_spec = importlib.util.spec_from_file_location("easel._video_provider_policy_shared", _SHARED)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

ProviderPolicyError = _mod.ProviderPolicyError
CredentialRef = _mod.CredentialRef
ProviderRef = _mod.ProviderRef
PricingEstimate = _mod.PricingEstimate
ApprovalReceipt = _mod.ApprovalReceipt
ProvenanceRecord = _mod.ProvenanceRecord
ProviderPolicy = _mod.ProviderPolicy
pricing_for = _mod.pricing_for
provenance_for = _mod.provenance_for
manifest_policy_json = _mod.manifest_policy_json
redact_mapping = _mod.redact_mapping
