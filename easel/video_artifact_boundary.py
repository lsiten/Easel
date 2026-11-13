"""Public import for the shared video artifact boundary contract."""
from pathlib import Path
import importlib.util
import sys
_SHARED = Path(__file__).resolve().parents[1] / "skills" / "shared" / "scripts" / "video_artifact_boundary.py"
_spec = importlib.util.spec_from_file_location("easel._video_artifact_boundary_shared", _SHARED)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
ArtifactBoundaryError = _mod.ArtifactBoundaryError
ArtifactRecord = _mod.ArtifactRecord
import_artifacts = _mod.import_artifacts
export_artifacts = _mod.export_artifacts
ARTIFACT_KINDS = _mod.ARTIFACT_KINDS
import_native_sources = _mod.import_native_sources
