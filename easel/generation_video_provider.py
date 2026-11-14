"""Native video protocol using configured endpoints and bounded owned downloads."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import mimetypes
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

import anyio
import httpx

from easel.channels.media_metadata import inspect_local_media
from easel.generation_models import GeneratedAsset, GenerationError
from easel.generation_video_models import ProviderVideo, VideoJob
from easel.harnesses.store import write_json
from easel.remote_media import download_to_file
from skills.shared.scripts.model_registry import env_aliases


def video_setting(settings: Mapping[str, str], name: str) -> str:
    return next((settings.get(key, '').strip() for key in (name, *env_aliases('video').get(name, ()))
                 if settings.get(key, '').strip()), '')


def video_base(settings: Mapping[str, str]) -> str:
    base = video_setting(settings, 'VIDEO_BASE_URL').rstrip('/')
    try:
        parsed = urlsplit(base)
        parsed.port
    except ValueError as error:
        raise GenerationError('video_configuration_invalid', 409) from error
    if any(ord(character) < 32 for character in base) or parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GenerationError('video_configuration_required', 409)
    if not video_setting(settings, 'VIDEO_API_KEY'):
        raise GenerationError('video_configuration_required', 409)
    return base


class NativeVideoProvider:
    def __init__(self, settings: Mapping[str, str]) -> None:
        self.base = video_base(settings)
        self.key = video_setting(settings, 'VIDEO_API_KEY')

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, follow_redirects=False,
            headers={'Authorization': 'Bearer ' + self.key},
            timeout=httpx.Timeout(connect=10, read=60, write=60, pool=10),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))

    async def submit(self, job: VideoJob, references: tuple[Path, ...]) -> ProviderVideo:
        async with self.client() as client:
            if job.source_id:
                response = await client.post(self.base + '/videos/' + job.source_id + '/remix',
                                             json={'prompt': job.request.prompt})
            else:
                values = {'prompt': job.request.prompt, 'model': job.runtime_model,
                          'seconds': job.request.seconds, 'size': job.request.size}
                fields = {key: (None, value) for key, value in values.items() if value}
                if references:
                    with references[0].open('rb') as stream:
                        response = await client.post(self.base + '/videos', files={
                            **fields, 'input_reference': (references[0].name, stream, mimetypes.guess_type(references[0].name)[0] or 'application/octet-stream')})
                else:
                    response = await client.post(self.base + '/videos', files=fields)
        response.raise_for_status()
        return self.receipt(response)

    def receipt(self, response: httpx.Response) -> ProviderVideo:
        result = ProviderVideo.model_validate_json(response.content)
        if result.error is not None:
            result = result.model_copy(update={'error': result.error.model_copy(update={
                'code': result.error.code.replace(self.key, '[redacted]'),
                'message': result.error.message.replace(self.key, '[redacted]')})})
        return result

    async def status(self, identifier: str) -> ProviderVideo:
        async with self.client() as client:
            response = await client.get(self.base + '/videos/' + identifier)
        response.raise_for_status()
        result = self.receipt(response)
        if result.id != identifier:
            raise GenerationError('video_provider_identity_mismatch', 502)
        return result

    async def archive(self, outputs: Path, job: VideoJob) -> GeneratedAsset:
        directory = outputs / ('AI视频-' + job.id)
        if directory.is_symlink():
            raise GenerationError('video_output_path_invalid')
        directory.mkdir(exist_ok=True)
        descriptor, name = tempfile.mkstemp(dir=directory, suffix='.mp4')
        import os
        os.close(descriptor)
        temporary = Path(name)
        try:
            url = (job.response.url or job.response.video_url) if job.response else ''
            if url:
                await anyio.to_thread.run_sync(lambda: download_to_file(url, temporary, max_bytes=2 * 1024**3, timeout=120))
            else:
                with anyio.fail_after(120):
                    async with self.client() as client, client.stream('GET', self.base + '/videos/' + job.provider_id + '/content') as response:
                        response.raise_for_status()
                        total = 0
                        async with await anyio.open_file(temporary, 'wb') as stream:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                total += len(chunk)
                                if total > 2 * 1024**3:
                                    raise GenerationError('video_result_too_large')
                                await stream.write(chunk)
            metadata = await anyio.to_thread.run_sync(inspect_local_media, temporary)
            if not metadata.width or not metadata.height or not metadata.duration_sec:
                raise GenerationError('video_result_invalid')
            digest = await anyio.to_thread.run_sync(self.hash_file, temporary)
            destination = directory / 'generated.mp4'
            temporary.replace(destination)
            write_json(directory / '.easel.json', {'profile': job.persona, 'generation_id': job.id, 'source': 'ai-video'})
            return GeneratedAsset(path=destination.relative_to(outputs).as_posix(), size=destination.stat().st_size,
                                  sha256=digest, kind='video')
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()
