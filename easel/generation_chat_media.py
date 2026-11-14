"""Archive generated inline images behind Easel's owned media boundary."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
from typing import Final
from urllib.parse import quote
from PIL import Image, UnidentifiedImageError
from pydantic import JsonValue
from easel.generation_chat_models import ChatRecord
from easel.generation_models import GeneratedAsset, GenerationError
from easel.generation_store import GenerationStore
from easel.harnesses.store import write_json

_DATA_IMAGE: Final = re.compile(r'data:image/(?:png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=]+')


def archive_images(value: JsonValue, record: ChatRecord, store: GenerationStore) -> JsonValue:
    """Keep text and structured content intact while replacing complete data images."""
    def replace(match: re.Match[str]) -> str:
        try:
            content = base64.b64decode(match[0].split(',', 1)[1], validate=True)
            if len(content) > 50 * 1024**2:
                raise GenerationError('chat_image_too_large', 502)
            with Image.open(io.BytesIO(content)) as picture:
                suffix = (picture.format or '').lower()
                picture.verify()
        except (binascii.Error, UnidentifiedImageError, OSError, ValueError):
            raise GenerationError('chat_image_invalid', 502) from None
        digest = hashlib.sha256(content).hexdigest()
        project = store.outputs / ('AI聊天-' + record.id)
        if project.is_symlink():
            raise GenerationError('chat_media_path_unsafe', 409)
        project.mkdir(exist_ok=True)
        destination = project / (digest + '.' + suffix)
        if destination.is_symlink():
            raise GenerationError('chat_media_path_unsafe', 409)
        destination.write_bytes(content)
        write_json(project / '.easel.json', {'profile': record.persona, 'generation_id': record.id})
        relative = destination.relative_to(store.outputs).as_posix()
        asset = GeneratedAsset(path=relative, sha256=digest, size=len(content))
        with store.connect() as db:
            position = int(digest[:12], 16)
            db.execute('INSERT OR IGNORE INTO generation_assets VALUES(?,?,?,?)',
                       (record.id, position, record.persona, asset.model_dump_json()))
        return '/api/media/' + quote(relative, safe='/')
    match value:
        case str():
            return _DATA_IMAGE.sub(replace, value)
        case list():
            return [archive_images(item, record, store) for item in value]
        case dict():
            return {key: archive_images(item, record, store) for key, item in value.items()}
        case _:
            return value
