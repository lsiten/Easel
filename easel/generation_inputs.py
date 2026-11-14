"""Import image URL/data references through the existing owned media boundary."""
from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path
from typing import Final
from urllib.parse import unquote, unquote_to_bytes, urlsplit
from typing_extensions import assert_never

from PIL import Image

from easel.content_library import ContentLibrary
from easel.content_library_import import RemoteContentImporter
from easel.content_library_models import ContentScope, RecordInput, RemoteImportInput
from easel.generation_models import GenerationError, GenerationJob, ImageRequest
from easel.harnesses.store import write_json
from easel.ownership import MediaManifestEntry, build_media_manifest, freeze_media, owned_media_path
from skills.shared.scripts.image_provider_config import MIME_MAP


MAX_REFERENCE_BYTES: Final = 50 * 1024**2


def external_reference(value: str) -> bool:
    return value[:8].lower().startswith(('http://', 'https://', 'data:'))


def local_image_request(request: ImageRequest, origin: str) -> ImageRequest:
    """Resolve this app's media URLs through ownership instead of a network fetch."""
    def relative(value: str) -> str:
        if value[:5].lower() == 'data:':
            return value
        try:
            parsed = urlsplit(value)
            base = urlsplit(origin)
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            base_port = base.port or (443 if base.scheme == 'https' else 80)
            same_origin = bool(origin) and parsed.scheme == base.scheme and parsed.hostname == base.hostname and port == base_port
            prefix = base.path.rstrip('/') + '/api/media/'
            if parsed.username is None and parsed.password is None and (
                    same_origin and parsed.path.startswith(prefix) or
                    not parsed.scheme and not parsed.netloc and parsed.path.startswith('/api/media/')):
                offset = len(prefix) if same_origin else len('/api/media/')
                decoded = unquote(parsed.path[offset:], errors='strict')
                if not decoded or '\0' in decoded:
                    raise GenerationError('generation_reference_invalid')
                return decoded
        except (ValueError, UnicodeDecodeError) as error:
            raise GenerationError('generation_reference_invalid') from error
        return value
    match request.image:
        case str() as path:
            image: str | tuple[str, ...] = relative(path)
        case tuple() as paths:
            image = tuple(relative(path) for path in paths)
        case unreachable:
            assert_never(unreachable)
    return request.model_copy(update={'image': image, 'mask': relative(request.mask) if request.mask else ''})


def _import_reference(outputs: Path, job: GenerationJob, reference: str, identity: str) -> str:
    library = ContentLibrary(outputs)
    scope = ContentScope(persona=job.persona, domain='media')
    with library.connect() as db:
        group = library.ensure_default(db, scope)
    if not reference[:5].lower().startswith('data:'):
        record = RemoteContentImporter(library).import_media(scope, RemoteImportInput(
            request_id=f'generation:{job.id}:{identity}', group_id=group.id,
            url=reference, kind='image', title='AI参考图片'))
        return record.path
    header, separator, encoded = reference.partition(',')
    if not separator or len(encoded) > MAX_REFERENCE_BYTES * 3:
        raise GenerationError('generation_data_uri_invalid')
    mime = header[5:].split(';', 1)[0].lower()
    suffix = next((suffix for suffix, value in MIME_MAP.items() if value == mime), '')
    if not suffix:
        raise GenerationError('generation_image_type_invalid')
    try:
        content = unquote_to_bytes(encoded)
        if header.lower().endswith(';base64'):
            cleaned = content.translate(None, b' \t\r\n\f')
            content = base64.b64decode(cleaned + b'=' * (-len(cleaned) % 4), validate=True)
    except (binascii.Error, ValueError) as error:
        raise GenerationError('generation_data_uri_invalid') from error
    if not content or len(content) > MAX_REFERENCE_BYTES:
        raise GenerationError('generation_data_uri_invalid')
    project = outputs / f'AI参考图-{job.id}-{identity[:12]}'
    project.mkdir()
    destination = project / f'image.{suffix}'
    destination.write_bytes(content)
    with Image.open(destination) as picture:
        picture.verify()
    write_json(project / '.easel.json', {'profile': job.persona, 'generation_id': job.id, 'source': 'image-reference'})
    return library.create(scope, RecordInput(group_id=group.id, title='AI参考图片', kind='image',
        path=destination.relative_to(outputs).as_posix())).path


def prepare_image_inputs(outputs: Path, job: GenerationJob) -> tuple[tuple[Path, ...], tuple[MediaManifestEntry, ...]]:
    """Freeze accepted local bytes and import external bytes before model execution."""
    local = freeze_media(outputs, job.persona, job.id, job.inputs)
    staged = {entry.relative_path: path for entry, path in zip(job.inputs, local)}
    imported: dict[str, Path] = {}
    entries: list[MediaManifestEntry] = []
    ordered = []
    references = (*job.request.images, *((job.request.mask,) if job.request.mask else ()))
    for reference in references:
        if not external_reference(reference):
            ordered.append(staged[reference])
            continue
        identity = hashlib.sha256(reference.encode()).hexdigest()
        if identity not in imported:
            path = _import_reference(outputs, job, reference, identity)
            _, actual = owned_media_path(outputs, job.persona, path)
            with Image.open(actual) as picture:
                picture.verify()
            manifest = build_media_manifest(outputs, job.persona, (path,))
            freeze_id = hashlib.sha256(f'{job.id}:{identity}'.encode()).hexdigest()[:32]
            imported[identity] = freeze_media(outputs, job.persona, freeze_id, manifest)[0]
            entries.extend(manifest)
        ordered.append(imported[identity])
    return tuple(ordered), tuple(entries)
