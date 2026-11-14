"""Serialized atomic updates of existing dotenv files."""
from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import tempfile

from skills.shared.scripts.asset_storage import _storage_lock


def update_environment(path: Path, updates: Mapping[str, str]) -> None:
    identity = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
    lock = Path(tempfile.gettempdir()) / f'easel-env-{identity}.lock'
    with _storage_lock(lock):
        lines = path.read_text(encoding='utf-8').splitlines() if path.is_file() else []
        seen = set()
        output = []
        for line in lines:
            key = line.strip().split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else ''
            if key not in updates:
                output.append(line)
                continue
            if key not in seen and updates[key].strip():
                output.append(f'{key}={updates[key]}')
            seen.add(key)
        output.extend(f'{key}={value}' for key, value in updates.items() if key not in seen and value.strip())
        descriptor, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                stream.write('\n'.join(output) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
