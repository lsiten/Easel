"""Optional private image receipts for the web generation worker."""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import TypeAlias

try:
    from .output_paths import validate_output_path
except ImportError:
    from output_paths import validate_output_path

Json: TypeAlias = str | int | float | bool | None | list['Json'] | dict[str, 'Json']


def save_image_receipt(payload: Mapping[str, Json]) -> None:
    value = os.environ.get('EASEL_IMAGE_RECEIPT', '')
    if not value:
        return
    target = validate_output_path(value, allow_system=True, create_parent=True)
    created = payload.get('created')
    if not isinstance(created, (int, float)) or isinstance(created, bool) or not math.isfinite(created) or created < 0:
        created = time.time()
    receipt: dict[str, Json] = {'created': created, 'revised_prompts': []}
    data = payload.get('data')
    if isinstance(data, list):
        prompts = []
        for item in data:
            prompt = item.get('revised_prompt', '') if isinstance(item, dict) else ''
            prompts.append(prompt if isinstance(prompt, str) else '')
        receipt['revised_prompts'] = prompts
    for name in ('background', 'output_format', 'quality', 'size'):
        candidate = payload.get(name)
        if isinstance(candidate, str):
            receipt[name] = candidate
    usage = payload.get('usage')
    if isinstance(usage, dict):
        counts = {}
        for name in ('input_tokens', 'output_tokens', 'total_tokens'):
            count = usage.get(name)
            if isinstance(count, (int, float)) and not isinstance(count, bool) and math.isfinite(count) and count >= 0:
                counts[name] = count
        receipt['usage'] = counts
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + '.', suffix='.tmp')
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(receipt, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
