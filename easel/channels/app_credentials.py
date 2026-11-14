"""Encrypted app-level token caches, separate from authenticated user credentials."""
from collections.abc import Callable
import hashlib
import json
import time
from threading import RLock

from cryptography.fernet import InvalidToken
from pydantic import TypeAdapter, ValidationError

from easel.channels.models import JsonDocument, PlatformId
from easel.channels.store import CredentialStore

_DOCUMENT = TypeAdapter(JsonDocument)


class AppCredentials:
    def __init__(self, platform: PlatformId, client_id: str, secret: str, store: CredentialStore | None = None) -> None:
        self.platform, self.client_id, self.store = platform, client_id, store
        self.fingerprint = hashlib.sha256((client_id + '\0' + secret).encode()).hexdigest()
        self.values: dict[str, tuple[float, JsonDocument]] = {}
        self.lock = RLock()
        if store:
            with store.connection() as db:
                db.execute('''CREATE TABLE IF NOT EXISTS app_credentials(platform TEXT NOT NULL,cache_key TEXT NOT NULL,
                    expires_at REAL NOT NULL,encrypted BLOB NOT NULL,PRIMARY KEY(platform,cache_key))''')

    def get(self, name: str, loader: Callable[[], tuple[JsonDocument, float]]) -> JsonDocument:
        with self.lock:
            if self.store is None: return self._get(name, loader)
            with self.store.platform_lock('_app:' + self.client_id, self.platform): return self._get(name, loader)

    def _get(self, name: str, loader: Callable[[], tuple[JsonDocument, float]]) -> JsonDocument:
        key = self.fingerprint + ':' + name
        current = self.values.get(key)
        if current and current[0] > time.time() + 300: return current[1]
        if self.store:
            with self.store.connection() as db:
                row = db.execute('SELECT expires_at,encrypted FROM app_credentials WHERE platform=? AND cache_key=?', (self.platform, key)).fetchone()
            if row and row['expires_at'] > time.time() + 300:
                try:
                    value = _DOCUMENT.validate_json(self.store.cipher.decrypt(row['encrypted']))
                    self.values[key] = row['expires_at'], value
                    return value
                except (InvalidToken, ValidationError):
                    self.clear(name)
        value, expires_in = loader()
        expiry = time.time() + expires_in
        self.values[key] = expiry, value
        if self.store:
            encrypted = self.store.cipher.encrypt(_DOCUMENT.dump_json(value))
            with self.store.connection() as db:
                db.execute('INSERT INTO app_credentials VALUES(?,?,?,?) ON CONFLICT(platform,cache_key) DO UPDATE SET expires_at=excluded.expires_at,encrypted=excluded.encrypted',
                    (self.platform, key, expiry, encrypted))
        return value

    def clear(self, name: str = '') -> None:
        prefix = self.fingerprint + ':' + name
        self.values = {key: value for key, value in self.values.items() if not key.startswith(prefix)}
        if self.store:
            with self.store.connection() as db:
                db.execute('DELETE FROM app_credentials WHERE platform=? AND cache_key LIKE ?', (self.platform, prefix + '%'))
