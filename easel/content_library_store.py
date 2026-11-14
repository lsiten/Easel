"""Transactional SQLite storage shared by local content operations."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
from collections.abc import Iterator

from easel.content_library_models import ContentError, ContentGroup, ContentRecord, ContentScope
from easel.persona import valid_persona_name


def content_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContentDatabase:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.directory = self.root / '_content'
        if self.directory.is_symlink():
            raise ContentError('content_store_path_unsafe')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'library.sqlite3'
        if self.path.is_symlink():
            raise ContentError('content_store_path_unsafe')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS content_groups(
                    id TEXT PRIMARY KEY, persona TEXT NOT NULL, domain TEXT NOT NULL,
                    is_default INTEGER NOT NULL, body TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS content_default_group
                    ON content_groups(persona,domain) WHERE is_default=1;
                CREATE TABLE IF NOT EXISTS content_records(
                    id TEXT PRIMARY KEY, persona TEXT NOT NULL, domain TEXT NOT NULL,
                    group_id TEXT NOT NULL REFERENCES content_groups(id), body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS content_records_scope ON content_records(persona,domain,group_id);
                CREATE TABLE IF NOT EXISTS content_usage(
                    persona TEXT NOT NULL, record_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    PRIMARY KEY(persona,record_id,run_id));
                CREATE TABLE IF NOT EXISTS content_imports(
                    persona TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
                    record_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(persona,request_id));
            ''')

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                yield db

    @staticmethod
    def owner(scope: ContentScope) -> None:
        if not valid_persona_name(scope.persona):
            raise ContentError('persona_invalid')

    def ensure_default(self, db: sqlite3.Connection, scope: ContentScope) -> ContentGroup:
        self.owner(scope)
        identifier = hashlib.sha256(f'{scope.persona}\0{scope.domain}'.encode()).hexdigest()[:32]
        group = ContentGroup(id=identifier, persona=scope.persona, domain=scope.domain,
                             name='默认分组', is_default=True, created_at=content_now())
        db.execute('INSERT OR IGNORE INTO content_groups VALUES(?,?,?,?,?)',
                   (identifier, scope.persona, scope.domain, 1, group.model_dump_json()))
        return self.group(db, scope, identifier)

    @staticmethod
    def group(db: sqlite3.Connection, scope: ContentScope, identifier: str) -> ContentGroup:
        row = db.execute('SELECT body FROM content_groups WHERE id=? AND persona=? AND domain=?',
                         (identifier, scope.persona, scope.domain)).fetchone()
        if row is None:
            raise ContentError('group_not_found', 404)
        return ContentGroup.model_validate_json(row['body'])

    @staticmethod
    def record(db: sqlite3.Connection, scope: ContentScope, identifier: str) -> ContentRecord:
        row = db.execute('SELECT body FROM content_records WHERE id=? AND persona=? AND domain=?',
                         (identifier, scope.persona, scope.domain)).fetchone()
        if row is None:
            raise ContentError('record_not_found', 404)
        return ContentRecord.model_validate_json(row['body'])

    @staticmethod
    def save_record(db: sqlite3.Connection, record: ContentRecord) -> None:
        db.execute('''INSERT INTO content_records VALUES(?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET group_id=excluded.group_id,body=excluded.body''',
            (record.id, record.persona, record.domain, record.group_id, record.model_dump_json()))
