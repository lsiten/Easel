"""Persist signed, expiring local upload grants and their state transitions."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid
from urllib.parse import urlencode
from typing_extensions import assert_never

from easel.content_library_models import ContentError
from easel.persona import valid_persona_name
from easel.upload_sessions_models import UploadAccess, UploadGrant, UploadRequest, UploadSession
from skills.shared.scripts.asset_storage import _storage_lock


class UploadStore:
    def __init__(self, outputs: Path) -> None:
        self.directory = outputs.resolve() / '_upload_sessions'
        if self.directory.is_symlink():
            raise ContentError('upload_store_path_unsafe')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'sessions.sqlite3'
        self.key_path = self.directory / '.signing-key'
        if self.path.is_symlink() or self.key_path.is_symlink():
            raise ContentError('upload_store_path_unsafe')
        lock = self.directory / '.signing-key.lock'
        if lock.is_symlink():
            raise ContentError('upload_store_path_unsafe')
        with _storage_lock(lock):
            try:
                descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                self.secret = self.key_path.read_bytes()
            else:
                with os.fdopen(descriptor, 'wb') as stream:
                    self.secret = secrets.token_bytes(32)
                    stream.write(self.secret)
                    stream.flush()
                    os.fsync(stream.fileno())
        if len(self.secret) != 32:
            raise ContentError('upload_signing_key_invalid', 500)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS upload_sessions(id TEXT PRIMARY KEY,persona TEXT NOT NULL,body TEXT NOT NULL)')

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            yield db

    def _token(self, session: UploadSession) -> str:
        fields = (session.id, session.persona, session.session_id, session.filename,
                  str(session.expires_at), str(session.size), session.sha256, str(session.public))
        # Existing attachment capabilities retain their original signature.
        match session.target:
            case 'attachment':
                signed_fields = fields
            case 'media':
                signed_fields = (*fields, 'target=media')
            case unreachable:
                assert_never(unreachable)
        return hmac.new(self.secret, '\0'.join(signed_fields).encode(), hashlib.sha256).hexdigest()

    def _get(self, db: sqlite3.Connection, identifier: str, access: UploadAccess) -> UploadSession:
        row = db.execute('SELECT body FROM upload_sessions WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ContentError('upload_not_found', 404)
        session = UploadSession.model_validate_json(row[0])
        if access.persona:
            if access.persona != session.persona:
                raise ContentError('upload_not_found', 404)
        elif not session.public or not hmac.compare_digest(access.token, self._token(session)):
            raise ContentError('upload_grant_invalid', 403)
        if session.expires_at <= time.time():
            raise ContentError('upload_grant_expired', 410)
        return session

    @staticmethod
    def _save(db: sqlite3.Connection, session: UploadSession) -> None:
        db.execute('INSERT INTO upload_sessions VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                   (session.id, session.persona, session.model_dump_json()))

    def issue(self, request: UploadRequest) -> UploadGrant:
        if not valid_persona_name(request.persona):
            raise ContentError('persona_invalid')
        if Path(request.filename).name != request.filename or '\\' in request.filename:
            raise ContentError('upload_filename_invalid')
        session = UploadSession(**request.model_dump(), id=uuid.uuid4().hex,
                                expires_at=int(time.time()) + request.expires_in)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute("SELECT COUNT(*) FROM upload_sessions WHERE persona=? AND json_extract(body,'$.expires_at')>? AND json_extract(body,'$.state')!='confirmed'",
                                (request.persona, time.time())).fetchone()[0]
            if active >= 100:
                raise ContentError('upload_session_limit', 429)
            self._save(db, session)
        token = self._token(session)
        prefix = f'/api/asset-uploads/public/{session.id}' if request.public else f'/api/asset-uploads/{session.id}'
        query = '?' + urlencode({'token': token} if request.public else {'persona': request.persona})
        return UploadGrant(id=session.id, token=token, expires_at=session.expires_at,
                           upload_url=prefix + '/content' + query, confirm_url=prefix + '/confirm' + query)

    def status(self, identifier: str, access: UploadAccess) -> UploadSession:
        with self.connection() as db:
            return self._get(db, identifier, access)

    def begin(self, identifier: str, access: UploadAccess) -> UploadSession:
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            session = self._get(db, identifier, access)
            active = session.receiver_pid > 0 and session.attempt_expires_at > time.time()
            if active:
                try:
                    os.kill(session.receiver_pid, 0)
                except ProcessLookupError:
                    active = False
                except PermissionError:
                    active = True
            if session.state != 'prepared' and (session.state != 'uploading' or active):
                raise ContentError('upload_state_conflict', 409)
            if session.state == 'uploading' and session.attempt_id:
                prefix = identifier + '-' + session.attempt_id + '-'
                for abandoned in self.data_path(identifier).parent.glob(prefix + '*'):
                    abandoned.unlink(missing_ok=True)
            session = session.model_copy(update={'state': 'uploading', 'receiver_pid': os.getpid(),
                'attempt_id': uuid.uuid4().hex, 'attempt_expires_at': min(session.expires_at, time.time() + 125)})
            self._save(db, session)
        return session

    def data_path(self, identifier: str) -> Path:
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise ContentError('upload_id_invalid')
        directory = self.directory / 'data'
        if directory.is_symlink():
            raise ContentError('upload_store_path_unsafe')
        directory.mkdir(exist_ok=True)
        destination = directory / identifier
        if destination.is_symlink():
            raise ContentError('upload_store_path_unsafe')
        return destination
