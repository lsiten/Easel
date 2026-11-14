"""Encrypted credentials and one-use OAuth state on local SQLite storage."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError
from skills.shared.scripts.asset_storage import _storage_lock

from easel.channels.models import ChannelError, Credential, OAuthPending, PlatformId


class CredentialStore:
    """Keep credentials encrypted at rest and bound to their persona/platform key."""

    def __init__(self, root: Path) -> None:
        self.root = root
        if root.is_symlink(): raise ChannelError('oauth', 'unsafe_store_path')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path = root / 'credentials.key'
        lock_path = root / 'credentials.lock'
        if lock_path.is_symlink(): raise ChannelError('oauth', 'unsafe_key_path')
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(descriptor)
        with _storage_lock(lock_path):
            if key_path.is_symlink(): raise ChannelError('oauth', 'unsafe_key_path')
            if not key_path.exists():
                descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
            self.cipher = Fernet(key_path.read_bytes())
        self.path = root / 'channels.sqlite3'
        if self.path.is_symlink(): raise ChannelError('oauth', 'unsafe_store_path')
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS credentials(
                    persona TEXT NOT NULL, platform TEXT NOT NULL, account_uid TEXT NOT NULL,
                    encrypted BLOB NOT NULL, PRIMARY KEY(persona,platform,account_uid));
                CREATE TABLE IF NOT EXISTS oauth_states(
                    state_hash TEXT PRIMARY KEY, persona TEXT NOT NULL, platform TEXT NOT NULL,
                    expires_at REAL NOT NULL, encrypted BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS oauth_sessions(state_hash TEXT PRIMARY KEY,persona TEXT NOT NULL,
                    platform TEXT NOT NULL,expires_at REAL NOT NULL,status TEXT NOT NULL,account_uid TEXT NOT NULL DEFAULT '');
            ''')
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, credential: Credential, *, oauth_state: str = '') -> None:
        with self.platform_lock(credential.persona, credential.platform):
            self._save(credential, oauth_state=oauth_state)

    def _save(self, credential: Credential, *, oauth_state: str = '') -> None:
        plain = credential.model_dump(mode='json')
        plain['access_token'] = credential.access_token.get_secret_value()
        plain['refresh_token'] = credential.refresh_token.get_secret_value()
        plain['auxiliary_tokens'] = credential.auxiliary_tokens
        ciphertext = self.cipher.encrypt(json.dumps(plain).encode())
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''INSERT INTO credentials VALUES(?,?,?,?)
                ON CONFLICT(persona,platform,account_uid) DO UPDATE SET encrypted=excluded.encrypted''',
                (credential.persona, credential.platform, credential.account_uid, ciphertext))
            if oauth_state:
                db.execute("UPDATE oauth_sessions SET status='connected',account_uid=? WHERE state_hash=? AND persona=? AND platform=?",
                    (credential.account_uid, hashlib.sha256(oauth_state.encode()).hexdigest(), credential.persona, credential.platform))

    @contextmanager
    def platform_lock(self, persona: str, platform: PlatformId) -> Iterator[None]:
        identity = hashlib.sha256(json.dumps([persona, platform]).encode()).hexdigest()
        lock_path = self.root / (identity + '.refresh.lock')
        if lock_path.is_symlink(): raise ChannelError(platform, 'unsafe_lock_path')
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(descriptor)
        with _storage_lock(lock_path):
            yield

    def get(self, persona: str, platform: PlatformId, account_uid: str) -> Credential:
        with self.connection() as db:
            row = db.execute('SELECT encrypted FROM credentials WHERE persona=? AND platform=? AND account_uid=?',
                             (persona, platform, account_uid)).fetchone()
        if row is None:
            raise ChannelError(platform, 'account_not_connected', status=404)
        try:
            credential = Credential.model_validate_json(self.cipher.decrypt(row['encrypted']))
        except (InvalidToken, ValidationError) as error:
            raise ChannelError(platform, 'credential_integrity_failed') from error
        if (credential.persona, credential.platform, credential.account_uid) != (persona, platform, account_uid):
            raise ChannelError(platform, 'credential_scope_mismatch', status=403)
        return credential

    def accounts(self, persona: str, platform: PlatformId) -> tuple[Credential, ...]:
        with self.connection() as db:
            rows = db.execute('SELECT account_uid FROM credentials WHERE persona=? AND platform=?', (persona, platform)).fetchall()
        return tuple(self.get(persona, platform, row['account_uid']) for row in rows)

    def delete(self, persona: str, platform: PlatformId, account_uid: str) -> None:
        with self.platform_lock(persona, platform):
            with self.connection() as db:
                db.execute('DELETE FROM credentials WHERE persona=? AND platform=? AND account_uid=?', (persona, platform, account_uid))

    def delete_many(self, persona: str, platform: PlatformId, account_uids: list[str]) -> None:
        with self.platform_lock(persona, platform):
            for uid in account_uids: self.get(persona, platform, uid)
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                db.executemany('DELETE FROM credentials WHERE persona=? AND platform=? AND account_uid=?',
                               [(persona, platform, uid) for uid in account_uids])

    def session_status(self, persona: str, platform: PlatformId, state: str) -> dict[str, str | float]:
        with self.connection() as db:
            row = db.execute('SELECT * FROM oauth_sessions WHERE state_hash=? AND persona=? AND platform=?',
                             (hashlib.sha256(state.encode()).hexdigest(), persona, platform)).fetchone()
        if row is None: raise ChannelError(platform, 'oauth_session_not_found', status=404)
        status = 'expired' if row['expires_at'] <= time.time() and row['status'] == 'pending' else row['status']
        return {'status': status, 'account_uid': row['account_uid'], 'expires_at': row['expires_at']}

    def fail_session(self, state: str, platform: PlatformId) -> None:
        with self.connection() as db:
            db.execute("UPDATE oauth_sessions SET status='failed' WHERE state_hash=? AND platform=? AND status='pending'",
                       (hashlib.sha256(state.encode()).hexdigest(), platform))

    def issue_state(self, pending: OAuthPending) -> str:
        state = secrets.token_urlsafe(32)
        plain = pending.model_dump(mode='json')
        plain['verifier'] = pending.verifier.get_secret_value()
        encrypted = self.cipher.encrypt(json.dumps(plain).encode())
        with self.connection() as db:
            db.execute('DELETE FROM oauth_states WHERE expires_at<?', (time.time(),))
            db.execute('INSERT INTO oauth_states VALUES(?,?,?,?,?)',
                       (hashlib.sha256(state.encode()).hexdigest(), pending.persona, pending.platform,
                        pending.expires_at, encrypted))
            db.execute('INSERT INTO oauth_sessions(state_hash,persona,platform,expires_at,status) VALUES(?,?,?,?,?)',
                       (hashlib.sha256(state.encode()).hexdigest(), pending.persona, pending.platform, pending.expires_at, 'pending'))
        return state

    def consume_state(self, state: str, platform: PlatformId) -> OAuthPending:
        digest = hashlib.sha256(state.encode()).hexdigest()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM oauth_states WHERE state_hash=? AND platform=?', (digest, platform)).fetchone()
            if row is None or row['expires_at'] <= time.time():
                raise ChannelError(platform, 'oauth_state_invalid', status=403)
            db.execute('DELETE FROM oauth_states WHERE state_hash=?', (digest,))
        try:
            pending = OAuthPending.model_validate_json(self.cipher.decrypt(row['encrypted']))
        except (InvalidToken, ValidationError) as error:
            raise ChannelError(platform, 'oauth_state_integrity_failed', status=403) from error
        if (pending.persona, pending.platform) != (row['persona'], platform):
            raise ChannelError(platform, 'oauth_state_scope_mismatch', status=403)
        return pending
