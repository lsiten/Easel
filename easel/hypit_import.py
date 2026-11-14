"""Import declared Hypit outputs into one owned content project per run."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

from easel.hypit_lifecycle import LifecycleRecord
from easel.video_artifact_boundary import ArtifactBoundaryError, import_artifacts
from easel.video_run_manifest import resolve_project_root
from skills.shared.scripts.asset_storage import _storage_lock


@dataclass(frozen=True, slots=True)
class ImportedArtifact:
    project: str
    output_path: str
    kind: str
    checksum: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def import_run_artifact(root: Path, record: LifecycleRecord, name: str) -> ImportedArtifact:
    """Only declared output paths under this run can enter its owned output project."""
    outputs = root.parent.resolve()
    workspace = Path(os.environ.get('EASEL_HYPIT_WORKSPACE_ROOT', str(outputs.parent))).expanduser().resolve()
    runtime_project = resolve_project_root(workspace, record.profile_id, record.session_id, record.id)
    allowed = ((root / 'runs' / record.id).resolve(), runtime_project.resolve())
    matches: list[tuple[Path, Path]] = []
    for value in record.outputs:
        source = Path(value).expanduser().resolve()
        if source.name != name or not source.is_file():
            continue
        boundary = next((directory for directory in allowed if source.is_relative_to(directory)), None)
        if boundary is not None:
            matches.append((source, boundary))
    if len(matches) != 1:
        raise ArtifactBoundaryError('artifact not found in this run')
    source, boundary = matches[0]
    kinds = {'.mp4': 'mp4', '.png': 'thumbnail', '.jpg': 'thumbnail', '.jpeg': 'thumbnail',
             '.webp': 'thumbnail', '.md': 'brief', '.txt': 'brief', '.html': 'report',
             '.pdf': 'report', '.json': 'report'}
    kind = kinds.get(source.suffix.lower())
    if kind is None:
        raise ArtifactBoundaryError('unsupported artifact type')
    identity = f'{record.profile_id}\0{record.session_id}\0{record.id}'
    project = outputs / f'hypit-{hashlib.sha256(identity.encode()).hexdigest()[:24]}'
    if project.is_symlink() or not project.resolve().is_relative_to(outputs):
        raise ArtifactBoundaryError('project path escapes output root')
    project.mkdir(parents=True, exist_ok=True)
    metadata = project / '.easel.json'
    digest = _sha256(source)
    relative = Path('derived') / record.profile_id / kind / source.name
    lock = project / '.import.lock'
    if lock.is_symlink():
        raise ArtifactBoundaryError('project import lock cannot be a symlink')
    with _storage_lock(lock):
        if metadata.is_symlink():
            raise ArtifactBoundaryError('project metadata cannot be a symlink')
        if metadata.exists():
            try:
                existing = json.loads(metadata.read_text(encoding='utf-8'))
            except (ValueError, OSError) as error:
                raise ArtifactBoundaryError('project metadata is unreadable') from error
            if not isinstance(existing, dict):
                raise ArtifactBoundaryError('project metadata is malformed')
            if (existing.get('profile'), existing.get('run_id')) != (record.profile_id, record.id):
                raise ArtifactBoundaryError('project owner or run mismatch')
        else:
            existing = {'profile': record.profile_id, 'run_id': record.id, 'session_id': record.session_id,
                        'engine': 'hypit', 'title': f'Hypit {record.id[:8]}', 'deliverables': []}
        target = project / relative
        if target.exists():
            if target.is_symlink() or _sha256(target) != digest:
                raise ArtifactBoundaryError('artifact already exists with different content')
        else:
            manifest = {'artifacts': [{'kind': kind, 'path': source.relative_to(boundary).as_posix(),
                        'checksum': digest, 'attribution': 'Hypit derived artifact'}]}
            import_artifacts(source_root=boundary, destination_root=project, source_engine='hypit',
                             target_profile=record.profile_id, manifest=manifest)
        deliverables = list(existing.get('deliverables', []))
        if relative.as_posix() not in deliverables:
            deliverables.append(relative.as_posix())
        existing['deliverables'] = deliverables
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=project, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(existing, stream, ensure_ascii=False, indent=2)
        try:
            os.replace(temporary, metadata)
        finally:
            temporary.unlink(missing_ok=True)
    return ImportedArtifact(project.name, f'{project.name}/{relative.as_posix()}', kind, digest)
