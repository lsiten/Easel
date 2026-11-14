"""Bounded upload receiving and retry-safe confirmation into existing attachment storage."""
from __future__ import annotations

from collections.abc import AsyncIterable, Callable
import hashlib
import os
from pathlib import Path
import tempfile
import time

import anyio

from easel.content_library_models import ContentError
from easel.upload_sessions_models import CompletedUpload, UploadAccess, UploadGrant, UploadReceipt, UploadRequest, UploadSession
from easel.upload_sessions_store import UploadStore


class UploadSessions(UploadStore):
    def __init__(self, outputs: Path, *, max_bytes: int, extensions: frozenset[str]) -> None:
        super().__init__(outputs)
        self.max_bytes, self.extensions = max_bytes, extensions

    def create(self, request: UploadRequest) -> UploadGrant:
        if request.size > self.max_bytes:
            raise ContentError('upload_size_limit', 413)
        if Path(request.filename).suffix.lower() not in self.extensions:
            raise ContentError('upload_extension_invalid')
        return self.issue(request)

    async def receive(self, identifier: str, access: UploadAccess, chunks: AsyncIterable[bytes]) -> UploadSession:
        session = await anyio.to_thread.run_sync(self.begin, identifier, access)
        temporary: Path | None = None
        completed = False
        try:
            destination = self.data_path(identifier)
            descriptor, name = tempfile.mkstemp(prefix=identifier + '-' + session.attempt_id + '-', dir=destination.parent)
            os.close(descriptor)
            temporary = Path(name)
            digest, size = hashlib.sha256(), 0
            with anyio.fail_after(120):
                async with await anyio.open_file(temporary, 'wb') as stream:
                    async for chunk in chunks:
                        size += len(chunk)
                        if size > min(session.size, self.max_bytes):
                            raise ContentError('upload_size_limit', 413)
                        digest.update(chunk)
                        await stream.write(chunk)
                    await stream.flush()
                    await anyio.to_thread.run_sync(os.fsync, stream.wrapped.fileno())
            if size != session.size or digest.hexdigest() != session.sha256:
                raise ContentError('upload_integrity_mismatch', 409)
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                current = self._get(db, identifier, access)
                if (current.state != 'uploading' or current.attempt_id != session.attempt_id
                        or current.attempt_expires_at <= time.time()):
                    raise ContentError('upload_state_conflict', 409)
                os.replace(temporary, destination)
                current = current.model_copy(update={'state': 'uploaded'})
                self._save(db, current)
            completed = True
            return current
        except TimeoutError as error:
            raise ContentError('upload_timeout', 408) from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if not completed:
                with self.connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute('SELECT body FROM upload_sessions WHERE id=?', (identifier,)).fetchone()
                    if row:
                        current = UploadSession.model_validate_json(row[0])
                        if current.state == 'uploading' and current.attempt_id == session.attempt_id:
                            self._save(db, current.model_copy(update={'state': 'prepared'}))

    def confirm(self, identifier: str, access: UploadAccess,
                accept_upload: Callable[[CompletedUpload], UploadReceipt]) -> UploadReceipt:
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            session = self._get(db, identifier, access)
            if session.state == 'confirmed' and session.receipt is not None:
                return session.receipt
            if session.state != 'uploaded':
                raise ContentError('upload_not_complete', 409)
            source = self.data_path(identifier)
            if not source.is_file() or source.stat().st_size != session.size:
                raise ContentError('upload_integrity_mismatch', 409)
            digest = hashlib.sha256()
            with source.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != session.sha256:
                raise ContentError('upload_integrity_mismatch', 409)
            receipt = accept_upload(CompletedUpload(path=source, session=session))
            if Path(receipt.path).is_absolute() or '..' in Path(receipt.path).parts:
                raise ContentError('upload_receipt_path_invalid', 500)
            self._save(db, session.model_copy(update={'state': 'confirmed', 'receipt': receipt}))
        return receipt
