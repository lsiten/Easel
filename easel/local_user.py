"""Transactional local user state scoped to an existing creator persona."""
from __future__ import annotations

from contextlib import closing, contextmanager
from collections.abc import Iterator
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from easel.content_library_models import ContentError
from easel.local_user_models import AIKind, AIPreference, AIPreferences, LocalUser, UserPatch
from easel.ownership import OwnershipError, owned_media_path
from easel.persona import valid_persona_name


class LocalUsers:
    def __init__(self, outputs: Path) -> None:
        self.outputs = outputs.resolve()
        self.directory = self.outputs / '_local_user'
        if self.directory.is_symlink():
            raise ContentError('user_store_path_unsafe')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'users.sqlite3'
        if self.path.is_symlink():
            raise ContentError('user_store_path_unsafe')
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS local_users(persona TEXT PRIMARY KEY, body TEXT NOT NULL)')

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            yield db

    @staticmethod
    def _read(db: sqlite3.Connection, persona: str) -> LocalUser:
        if not valid_persona_name(persona):
            raise ContentError('persona_invalid')
        row = db.execute('SELECT body FROM local_users WHERE persona=?', (persona,)).fetchone()
        return LocalUser.model_validate_json(row[0]) if row else LocalUser(persona=persona, name=persona)

    @staticmethod
    def _save(db: sqlite3.Connection, value: LocalUser) -> None:
        db.execute('INSERT INTO local_users VALUES(?,?) ON CONFLICT(persona) DO UPDATE SET body=excluded.body',
                   (value.persona, value.model_dump_json()))

    def read(self, persona: str) -> LocalUser:
        with self.connection() as db:
            return self._read(db, persona)

    def update(self, persona: str, patch: UserPatch) -> LocalUser:
        if patch.avatar:
            parsed = urlsplit(patch.avatar)
            if parsed.scheme:
                if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                    raise ContentError('avatar_url_invalid')
            else:
                try:
                    owned_media_path(self.outputs, persona, patch.avatar)
                except OwnershipError as error:
                    raise ContentError('avatar_ownership_invalid', 403) from error
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self._read(db, persona)
            values = {key: value for key, value in patch.model_dump(exclude_unset=True).items() if value is not None}
            user = LocalUser.model_validate({**old.model_dump(), **values})
            self._save(db, user)
        return user

    def set_ai(self, persona: str, preferences: AIPreferences) -> LocalUser:
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self._read(db, persona)
            user = old.model_copy(update={'ai': preferences})
            self._save(db, user)
        return user

    def set_ai_kind(self, persona: str, kind: AIKind, preference: AIPreference) -> LocalUser:
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self._read(db, persona)
            preferences = old.ai.model_copy(update={kind: preference})
            user = old.model_copy(update={'ai': preferences})
            self._save(db, user)
        return user
