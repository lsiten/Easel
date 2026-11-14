"""Idempotent public-URL media ingestion into explicitly owned content projects."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
import uuid

from easel.content_library import ContentLibrary
from easel.content_library_models import ContentError, ContentRecord, ContentScope, RecordInput, RemoteImportInput
from easel.content_library_store import content_now
from easel.reference_output import bind_reference_owner, safe_reference_metadata, source_digest
from easel.remote_media import RemoteMediaError, download_to_file, validate_public_url
from skills.shared.scripts.asset_storage import _storage_lock


class RemoteContentImporter:
    def __init__(self, library: ContentLibrary) -> None:
        self.library = library

    def import_media(self, scope: ContentScope, payload: RemoteImportInput) -> ContentRecord:
        if scope.domain != 'media':
            raise ContentError('remote_import_requires_media')
        library = self.library
        library.get_group(scope, payload.group_id)
        digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        identity = hashlib.sha256(f'{scope.persona}\0{payload.request_id}'.encode()).hexdigest()
        lock = library.directory / (identity + '.import.lock')
        if lock.is_symlink():
            raise ContentError('import_lock_unsafe')
        with _storage_lock(lock):
            with library.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT OR IGNORE INTO content_imports(persona,request_id,digest) VALUES(?,?,?)',
                           (scope.persona, payload.request_id, digest))
                row = db.execute('SELECT * FROM content_imports WHERE persona=? AND request_id=?',
                                 (scope.persona, payload.request_id)).fetchone()
                if row['digest'] != digest:
                    raise ContentError('import_request_conflict', 409)
                if row['record_id']:
                    return library.record(db, scope, row['record_id'])
            try:
                validate_public_url(payload.url)
            except RemoteMediaError as error:
                raise ContentError('remote_media_download_rejected') from error
            project = library.root / ('import-' + identity[:24])
            if project.is_symlink():
                raise ContentError('import_project_unsafe')
            project.mkdir(exist_ok=True)
            try:
                bind_reference_owner(project, library.root, scope.persona)
            except (ValueError, OSError) as error:
                raise ContentError('import_project_owner_invalid', 403) from error
            extensions = {'image': {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif'},
                          'video': {'.mp4', '.mov', '.webm', '.m4v', '.mkv'},
                          'audio': {'.mp3', '.wav', '.m4a', '.ogg', '.flac'}}
            suffix = Path(urlsplit(payload.url).path).suffix.lower()
            if suffix not in extensions[payload.kind]:
                suffix = {'image': '.png', 'video': '.mp4', 'audio': '.mp3'}[payload.kind]
            destination = project / ('media' + suffix)
            try:
                download_to_file(payload.url, destination, max_bytes=50 * 1024**2, timeout=120)
            except RemoteMediaError as error:
                raise ContentError('remote_media_download_rejected', 400) from error
            metadata = project / '.easel.json'
            document = json.loads(metadata.read_text(encoding='utf-8'))
            document.update({'source': safe_reference_metadata(payload.url, project),
                             'source_sha256': source_digest(destination), 'import_request_digest': digest})
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=project, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(document, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.replace(temporary, metadata)
            finally:
                temporary.unlink(missing_ok=True)
            values = RecordInput(group_id=payload.group_id, title=payload.title, description=payload.description,
                                 kind=payload.kind, path=destination.relative_to(library.root).as_posix())
            with library.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                library._validate_input(db, scope, values)
                stamp = content_now()
                record = ContentRecord(**values.model_dump(), id=uuid.uuid4().hex, persona=scope.persona,
                                       domain='media', created_at=stamp, updated_at=stamp)
                library.save_record(db, record)
                db.execute('UPDATE content_imports SET record_id=? WHERE persona=? AND request_id=?',
                           (record.id, scope.persona, payload.request_id))
            return record
