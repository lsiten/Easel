"""Signed delivery of the exact owned bytes approved for publication."""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import re
import time
from collections.abc import Mapping
from urllib.parse import urlencode, urlsplit

from easel.channels.models import ChannelError, Credential
from easel.ownership import MediaManifestEntry


def credential_revision(credential: Credential) -> str:
    """Bind approval to the full stored authorization revision without persisting tokens."""
    value = credential.model_dump_json()
    secret = credential.access_token.get_secret_value() + '\0' + credential.refresh_token.get_secret_value()
    return hashlib.sha256((value + secret).encode()).hexdigest()


def signing_key(root: Path) -> bytes:
    """Reuse the persistent credential encryption key as domain-separated HMAC material."""
    return hashlib.sha256(b'easel-channel-publication-v1\0' + (root / '_channels' / 'credentials.key').read_bytes()).digest()


def approval_signature(root: Path, message: str) -> str:
    return hmac.new(signing_key(root), b'approval\0' + message.encode(), hashlib.sha256).hexdigest()


def media_base(settings: Mapping[str, str]) -> str:
    value = settings.get('EASEL_CHANNEL_MEDIA_BASE_URL', '').rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ChannelError('media', 'public_media_base_required', status=409)
    return value


def media_signature(root: Path, run_id: str, persona: str, index: int, digest: str, expires: int) -> str:
    message = f'{run_id}\0{persona}\0{index}\0{digest}\0{expires}'
    return hmac.new(signing_key(root), b'media\0' + message.encode(), hashlib.sha256).hexdigest()


def media_url(root: Path, base: str, run_id: str, persona: str, index: int, digest: str) -> str:
    expires = int(time.time()) + 3600
    query = urlencode({'persona': persona, 'expires': expires,
                       'signature': media_signature(root, run_id, persona, index, digest, expires)})
    return f'{base}/api/channel-publish/media/{run_id}/{index}?{query}'


def frozen_paths(root: Path, persona: str, run_id: str, manifest: tuple[MediaManifestEntry, ...]) -> tuple[Path, ...]:
    """Resolve only generated freeze locations, rejecting moved or altered approved media."""
    if not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise ChannelError('media', 'run_identity_invalid', status=404)
    result: list[Path] = []
    for index, entry in enumerate(manifest):
        path = root / '_publish' / persona / run_id / 'media' / f'{index:02d}{Path(entry.relative_path).suffix.lower()}'
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ChannelError('media', 'frozen_media_missing', status=409)
        hasher = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(chunk)
        if hasher.hexdigest() != entry.sha256 or path.stat().st_size != entry.size:
            raise ChannelError('media', 'frozen_media_changed', status=409)
        result.append(path)
    return tuple(result)
