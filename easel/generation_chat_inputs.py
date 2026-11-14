"""Resolve owned local chat media without fetching ordinary external URLs."""
from __future__ import annotations

import base64
import hashlib
import io
import mimetypes
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote, urlsplit

from PIL import Image
from pydantic import JsonValue
from typing_extensions import assert_never

from easel.channels.media_metadata import inspect_local_media
from easel.generation_chat_models import ChatRecord
from easel.generation_inputs import MAX_REFERENCE_BYTES
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.generation_video_grants import VideoMediaGrants, validate_media_base
from easel.ownership import MediaManifestEntry, build_media_manifest, freeze_media, owned_media_path

_TOKENS: Final = re.compile(
    r'https?://[^\s\"\'<>()[\]{},]+|/api/media/[^\s\"\'<>()[\]{},]+'
    r'|(?<![\w/])[^\s\"\'<>()[\]{},:]+/[^\s\"\'<>()[\]{},:]+'
    r'\.(?:png|jpe?g|gif|webp|bmp|tiff?|mp4|webm|mov|m4v|mp3|wav|m4a|ogg|flac|aac)(?!\w)', re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ChatInputContext:
    store: GenerationStore
    persona: str
    origin: str = ''
    public_base: str = ''


def _local_reference(value: str, context: ChatInputContext) -> str | None:
    """Only recognize app media URLs and relative media paths; never fetch URLs."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            for origin in (context.origin, context.public_base):
                base = urlsplit(origin)
                prefix = base.path.rstrip('/') + '/api/media/'
                port = parsed.port or (443 if parsed.scheme == 'https' else 80)
                base_port = base.port or (443 if base.scheme == 'https' else 80)
                if (origin and parsed.scheme == base.scheme and parsed.hostname == base.hostname and port == base_port
                        and parsed.username is None and parsed.password is None and parsed.path.startswith(prefix)):
                    return unquote(parsed.path[len(prefix):], errors='strict')
            return None
        path = parsed.path.removeprefix('/api/media/')
        return unquote(path, errors='strict')
    except (ValueError, UnicodeDecodeError) as error:
        raise GenerationError('chat_media_reference_invalid') from error


def _replace(value: JsonValue, replacements: dict[str, str]) -> JsonValue:
    match value:
        case str():
            return _TOKENS.sub(lambda match: replacements.get(match[0], match[0]), value)
        case list():
            return [_replace(item, replacements) for item in value]
        case dict():
            return {key: _replace(item, replacements) for key, item in value.items()}
        case int() | float() | bool() | None:
            return value
        case unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class PreparedChatInputs:
    body: dict[str, JsonValue]
    context: ChatInputContext
    references: dict[str, str]
    media: tuple[MediaManifestEntry, ...]

    def bind(self, record: ChatRecord) -> dict[str, JsonValue]:
        """Bind validated AV references to immutable, expiring job capability URLs."""
        if not self.media:
            return self.body
        paths = freeze_media(self.context.store.outputs, record.persona, record.id, self.media)
        grants = VideoMediaGrants(self.context.store)
        urls = {entry.relative_path: grants.create(record, (path, entry), self.context.public_base)
                for path, entry in zip(paths, self.media)}
        replacements = {reference: urls[path] for reference, path in self.references.items() if path in urls}
        return {key: _replace(value, replacements) for key, value in self.body.items()}


def prepare_chat_inputs(body: dict[str, JsonValue], context: ChatInputContext) -> PreparedChatInputs:
    """Validate every local owner before reading bytes or claiming a paid operation."""
    references: dict[str, str] = {}

    def collect(value: JsonValue) -> None:
        match value:
            case str():
                if value.startswith('data:'):
                    return
                for token in _TOKENS.finditer(value):
                    relative = _local_reference(token[0], context)
                    if relative is not None:
                        references[token[0]] = relative
            case list():
                for item in value:
                    collect(item)
            case dict():
                for item in value.values():
                    collect(item)
            case int() | float() | bool() | None:
                return
            case unreachable:
                assert_never(unreachable)

    collect(body)
    if any('\0' in path or not path for path in references.values()):
        raise GenerationError('chat_media_reference_invalid')
    canonical = {reference: owned_media_path(context.store.outputs, context.persona, path)[1]
                 .relative_to(context.store.outputs.resolve()).as_posix() for reference, path in references.items()}
    entries = build_media_manifest(context.store.outputs, context.persona, tuple(canonical.values()))
    replacements: dict[str, str] = {}
    media = []
    for entry in entries:
        path = context.store.outputs / entry.relative_path
        mime = mimetypes.guess_type(path.name)[0] or ''
        if mime.startswith('image/'):
            if entry.size > MAX_REFERENCE_BYTES:
                raise GenerationError('chat_media_image_too_large')
            data = path.read_bytes()
            if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
                raise GenerationError('chat_media_reference_changed', 409)
            try:
                with Image.open(io.BytesIO(data)) as picture:
                    actual_mime = Image.MIME.get(picture.format, '')
                    picture.verify()
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                raise GenerationError('chat_media_image_invalid') from error
            if not actual_mime.startswith('image/'):
                raise GenerationError('chat_media_image_invalid')
            replacements[entry.relative_path] = 'data:' + actual_mime + ';base64,' + base64.b64encode(data).decode()
        elif mime.startswith(('video/', 'audio/')):
            validate_media_base(context.public_base)
            metadata = inspect_local_media(path)
            if not metadata.duration_sec or mime.startswith('video/') and not (metadata.width and metadata.height):
                raise GenerationError('chat_media_reference_invalid')
            media.append(entry)
        else:
            raise GenerationError('chat_media_type_invalid')
    mapped = {reference: replacements[path] for reference, path in canonical.items() if path in replacements}
    prepared = {key: _replace(value, mapped) for key, value in body.items()}
    return PreparedChatInputs(prepared, context, canonical, tuple(media))
