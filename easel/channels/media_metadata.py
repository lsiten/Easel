"""Best-effort local metadata using Easel's existing FFprobe installation."""
from __future__ import annotations

import math
from pathlib import Path
import shutil
import subprocess

from pydantic import TypeAdapter, ValidationError

from easel.channels.models import JsonDocument, MediaMetadata

_DOCUMENT = TypeAdapter(JsonDocument)


def inspect_local_media(path: Path) -> MediaMetadata:
    base = MediaMetadata(size_bytes=path.stat().st_size, format=path.suffix)
    executable = shutil.which('ffprobe')
    if executable is None: return base
    try:
        result = subprocess.run([executable, '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-select_streams', 'v:0', '-show_entries', 'stream=width,height,duration:format=duration',
            '-of', 'json', str(path.resolve())], stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False)
        if result.returncode: return base
        data = _DOCUMENT.validate_json(result.stdout)
        streams = data.get('streams')
        stream = streams[0] if isinstance(streams, list) and streams and isinstance(streams[0], dict) else {}
        container = data.get('format') if isinstance(data.get('format'), dict) else {}
        duration = stream.get('duration') or container.get('duration')
        seconds = float(duration) if type(duration) in (str, int, float) else None
        return MediaMetadata(size_bytes=base.size_bytes, format=base.format,
            width=stream.get('width'), height=stream.get('height'),
            duration_sec=seconds if seconds is not None and math.isfinite(seconds) and seconds >= 0 else None)
    except (OSError, subprocess.TimeoutExpired, ValidationError, ValueError):
        return base
