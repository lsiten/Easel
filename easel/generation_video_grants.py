"""Expiring opaque access to exact frozen local video/audio references."""
import hashlib
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit
from typing import Literal
from typing_extensions import assert_never

from easel.generation_models import GenerationError, GenerationModel
from easel.generation_store import GenerationStore
from easel.generation_video_models import VideoJob
from easel.generation_video_provider import NativeVideoProvider
from easel.ownership import MediaManifestEntry
from easel.generation_chat_models import ChatRecord


def validate_media_base(base: str) -> None:
    """Require an explicitly configured HTTPS base for capability URLs."""
    try:
        parsed = urlsplit(base)
    except ValueError as error:
        raise GenerationError('video_public_media_base_required', 409) from error
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GenerationError('video_public_media_base_required', 409)


class FrozenVideoReference(GenerationModel):
    path: str
    persona: str
    job_id: str
    sha256: str
    size: int
    expires_at: float
    job_kind: Literal['video', 'chat'] = 'video'


class VideoMediaGrants:
    def __init__(self, store: GenerationStore) -> None:
        self.store = store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS video_media_grants(
                token_hash TEXT PRIMARY KEY,job_id TEXT NOT NULL,persona TEXT NOT NULL,expires_at REAL NOT NULL,body TEXT NOT NULL)''')
            db.execute('DELETE FROM video_media_grants WHERE expires_at<?', (time.time(),))

    def create(self, job: VideoJob | ChatRecord, source: tuple[Path, MediaManifestEntry], base: str) -> str:
        validate_media_base(base)
        path, entry = source
        token = secrets.token_urlsafe(32)
        match job:
            case ChatRecord():
                kind: Literal['video', 'chat'] = 'chat'
            case VideoJob():
                kind = 'video'
            case unreachable:
                assert_never(unreachable)
        grant = FrozenVideoReference(path=path.relative_to(self.store.outputs).as_posix(), persona=job.persona,
            job_id=job.id, sha256=entry.sha256, size=entry.size, expires_at=time.time() + 3600,
            job_kind=kind)
        with self.store.connect() as db:
            db.execute('INSERT INTO video_media_grants VALUES(?,?,?,?,?)',
                (hashlib.sha256(token.encode()).hexdigest(), job.id, job.persona, grant.expires_at, grant.model_dump_json()))
        return base.rstrip('/') + '/api/ai/video/media/' + token

    def resolve(self, token: str) -> Path:
        if len(token) != 43:
            raise GenerationError('video_media_grant_invalid', 403)
        with self.store.connect() as db:
            row = db.execute('''SELECT body FROM video_media_grants WHERE token_hash=? AND expires_at>?''',
                (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if row is None:
            raise GenerationError('video_media_grant_invalid', 403)
        grant = FrozenVideoReference.model_validate_json(row['body'])
        match grant.job_kind:
            case 'chat':
                table, states = 'chat_jobs', ('running', 'succeeded')
            case 'video':
                table, states = 'video_jobs', ('queued', 'submitting', 'waiting', 'succeeded')
            case unreachable:
                assert_never(unreachable)
        with self.store.connect() as db:
            owner = db.execute(f'SELECT state FROM {table} WHERE id=? AND persona=?', (grant.job_id, grant.persona)).fetchone()
        if owner is None or owner['state'] not in states:
            raise GenerationError('video_media_grant_invalid', 403)
        path = self.store.outputs / grant.path
        expected = self.store.outputs / '_publish' / grant.persona / grant.job_id / 'media'
        root = self.store.outputs.resolve()
        if (not expected.resolve().is_relative_to(root) or any(parent.is_symlink() for parent in (path, *path.parents) if parent != root and root in parent.parents)
                or path.resolve().parent != expected.resolve() or not path.is_file()):
            raise GenerationError('video_media_grant_changed', 409)
        if path.stat().st_size != grant.size or NativeVideoProvider.hash_file(path) != grant.sha256:
            raise GenerationError('video_media_grant_changed', 409)
        return path
