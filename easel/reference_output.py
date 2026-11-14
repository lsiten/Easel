"""Ownership and path-safe metadata for reference-analysis output."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from pydantic import JsonValue

from easel.persona import valid_persona_name


def bind_reference_owner(directory: Path, outputs: Path, persona: str) -> None:
    from skills.shared.scripts.project_scope import record_profile
    from skills.shared.scripts.asset_storage import _storage_lock
    if not valid_persona_name(persona):
        raise ValueError('参考分析画像名称非法')
    resolved = directory.resolve()
    root = outputs.resolve()
    if resolved == root:
        raise ValueError('参考分析需要独立项目目录')
    project = root / resolved.relative_to(root).parts[0] if resolved.is_relative_to(root) else resolved
    metadata = project / '.easel.json'
    lock = project / '.reference-owner.lock'
    if lock.is_symlink():
        raise ValueError('项目所有权锁不能是符号链接')
    with _storage_lock(lock):
        if metadata.is_symlink():
            raise ValueError('项目元数据不能是符号链接')
        if metadata.exists():
            existing = json.loads(metadata.read_text(encoding='utf-8'))
            if not isinstance(existing, dict) or existing.get('profile') not in ('', None, persona):
                raise ValueError('参考分析项目不属于当前画像')
        record_profile(project, persona)


def safe_reference_metadata(value: JsonValue, directory: Path) -> JsonValue:
    """Retain content while converting filesystem paths and URL credentials."""
    match value:
        case dict():
            return {key: safe_reference_metadata(item, directory) for key, item in value.items()}
        case list():
            return [safe_reference_metadata(item, directory) for item in value]
        case str():
            if value.startswith(('http://', 'https://')):
                parsed = urlsplit(value)
                host = parsed.hostname or ''
                if parsed.port:
                    host += f':{parsed.port}'
                return urlunsplit((parsed.scheme, host, parsed.path, '', ''))
            if Path(value).is_absolute():
                path = Path(value).resolve()
                return path.relative_to(directory.resolve()).as_posix() if path.is_relative_to(directory.resolve()) else path.name
            return value
        case _:
            return value


def source_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
