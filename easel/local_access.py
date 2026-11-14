"""Persona-scoped caller keys and durable short links."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
import hashlib
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit


class AccessStore:
    def __init__(self, root: Path) -> None:
        if root.is_symlink():
            raise ValueError('访问存储目录不能是符号链接')
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'access.sqlite3'
        if self.path.is_symlink():
            raise ValueError('访问存储不能是符号链接')
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS api_keys(
                    id TEXT PRIMARY KEY, persona TEXT NOT NULL, name TEXT NOT NULL,
                    secret_hash TEXT UNIQUE NOT NULL, permission TEXT NOT NULL,
                    created_at REAL NOT NULL,last_used_at REAL);
                CREATE TABLE IF NOT EXISTS short_links(
                    code TEXT PRIMARY KEY,persona TEXT NOT NULL,url TEXT NOT NULL,
                    created_at REAL NOT NULL,expires_at REAL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_key(self, persona: str, name: str, permission: str = 'read') -> dict:
        if permission not in ('read', 'write') or not name.strip():
            raise ValueError('名称和权限无效')
        token = 'eak_' + secrets.token_urlsafe(32)
        key_id = secrets.token_hex(16)
        created = time.time()
        with self.connection() as db:
            db.execute('INSERT INTO api_keys VALUES(?,?,?,?,?,?,NULL)',
                       (key_id, persona, name.strip(), hashlib.sha256(token.encode()).hexdigest(), permission, created))
        return {'id': key_id, 'name': name.strip(), 'key': token, 'permission': permission, 'created_at': created}

    def keys(self, persona: str) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute('''SELECT id,name,permission,created_at,last_used_at
                FROM api_keys WHERE persona=? ORDER BY created_at DESC''', (persona,))]

    def revoke(self, persona: str, key_id: str) -> bool:
        with self.connection() as db:
            return db.execute('DELETE FROM api_keys WHERE persona=? AND id=?', (persona, key_id)).rowcount == 1

    def authenticate(self, token: str) -> dict | None:
        if not token.startswith('eak_') or len(token) > 128:
            return None
        with self.connection() as db:
            row = db.execute('SELECT id,persona,permission FROM api_keys WHERE secret_hash=?',
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if row is None:
                return None
            db.execute('UPDATE api_keys SET last_used_at=? WHERE id=?', (time.time(), row['id']))
            return dict(row)

    def create_link(self, persona: str, url: str, expires_at: float | None = None) -> str:
        try:
            parsed = urlsplit(url)
        except ValueError as error:
            raise ValueError('短链目标无效') from error
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('短链目标必须为无凭证 HTTP(S) URL')
        if len(url) > 8192 or any(ord(char) < 32 for char in url):
            raise ValueError('短链目标无效')
        if expires_at is not None and expires_at <= time.time():
            raise ValueError('到期时间必须在未来')
        code = secrets.token_urlsafe(9)
        with self.connection() as db:
            db.execute('INSERT INTO short_links VALUES(?,?,?,?,?)', (code, persona, url, time.time(), expires_at))
        return code

    def resolve(self, code: str) -> str | None:
        with self.connection() as db:
            row = db.execute('SELECT url FROM short_links WHERE code=? AND (expires_at IS NULL OR expires_at>?)',
                             (code, time.time())).fetchone()
            return row['url'] if row else None

    def create_handoff_link(self, persona: str, scheme: str, expires_at: float) -> str:
        parsed = urlsplit(scheme)
        if parsed.scheme != 'snssdk1128' or parsed.netloc != 'openplatform' or parsed.path != '/share' or not parsed.query:
            raise ValueError('不支持的 App 发布链接')
        code = secrets.token_urlsafe(9)
        with self.connection() as db:
            db.execute('INSERT INTO short_links VALUES(?,?,?,?,?)', (code, persona, scheme, time.time(), expires_at))
        return code

    def links(self, persona: str) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT code,url,created_at,expires_at FROM short_links WHERE persona=?', (persona,))]

    def delete_link(self, persona: str, code: str) -> bool:
        with self.connection() as db:
            return db.execute('DELETE FROM short_links WHERE persona=? AND code=?', (persona, code)).rowcount == 1
