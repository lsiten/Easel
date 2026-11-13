"""Public import for the shared video run manifest contract."""
from pathlib import Path
import importlib.util
import sys

_SHARED = Path(__file__).resolve().parents[1] / "skills" / "shared" / "scripts" / "video_run_manifest.py"
_spec = importlib.util.spec_from_file_location("easel._video_run_manifest_shared", _SHARED)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

ManifestError = _mod.ManifestError
VideoRunManifest = _mod.VideoRunManifest
resolve_project_root = _mod.resolve_project_root
read_openfilm_session_pointer = _mod.read_openfilm_session_pointer
is_public_artifact = _mod.is_public_artifact
MANIFEST_VERSION = _mod.MANIFEST_VERSION
SUPPORTED_ENGINES = _mod.SUPPORTED_ENGINES
