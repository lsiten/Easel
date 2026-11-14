"""Profile-owned frozen Aideo inputs and validated video outputs."""
import hashlib
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit
import anyio
from pydantic import JsonValue
from easel.channels.media_metadata import inspect_local_media
from easel.generation_aideo_models import AideoJob, StreamInput, UrlInput, VidInput
from easel.generation_aideo_provider import VodCall, VodProvider, mapping, sequence, string
from easel.generation_models import GeneratedAsset, GenerationError
from easel.generation_video_provider import NativeVideoProvider
from easel.harnesses.store import write_json
from easel.ownership import owned_media_path, project_identity
from easel.remote_media import download_to_file

LIMIT = 1000 * 1024**2


class OwnedVideoJob(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def persona(self) -> str: ...

async def input_vids(outputs: Path, job: AideoJob, provider: VodProvider) -> list[JsonValue]:
    result: list[JsonValue] = []
    for item in job.request.multiInputs:
        if isinstance(item, VidInput):
            result.append({'Type': 'Vid', 'Vid': item.vid})
            continue
        reference = item.stream if isinstance(item, StreamInput) else item.url if isinstance(item, UrlInput) else item
        if reference.startswith('vid://'):
            vid = VidInput(type='vid', vid=reference[6:]).vid
        else:
            with tempfile.TemporaryDirectory(prefix='aideo-input-') as directory:
                name = item.fileName if isinstance(item, (StreamInput, UrlInput)) and item.fileName else 'input'
                extension = item.fileExtension if isinstance(item, (StreamInput, UrlInput)) and item.fileExtension else '.mp4'
                if not extension.startswith('.'):
                    extension = '.' + extension
                if '/' in extension or '\\' in extension or len(extension) > 12:
                    raise GenerationError('aideo_input_extension_invalid', 422)
                target = Path(directory) / (Path(name).stem + extension)
                if reference.startswith(('https://', 'http://')):
                    await anyio.to_thread.run_sync(lambda: download_to_file(reference, target, max_bytes=LIMIT, timeout=120))
                else:
                    source = owned_media_path(outputs, job.persona, reference)[1]
                    if source.stat().st_size > LIMIT:
                        raise GenerationError('aideo_input_too_large', 413)
                    await anyio.to_thread.run_sync(shutil.copyfile, source, target)
                if isinstance(item, StreamInput) and target.stat().st_size != item.fileSize:
                    raise GenerationError('aideo_input_size_mismatch', 409)
                await verify_video(target)
                vid = await provider.upload(target, job.space)
        result.append({'Type': 'Vid', 'Vid': vid})
    return result


async def verify_video(path: Path) -> None:
    metadata = await anyio.to_thread.run_sync(inspect_local_media, path)
    if not metadata.width or not metadata.height or not metadata.duration_sec:
        raise GenerationError('aideo_media_invalid', 422)


async def resolve_video(video: dict[str, JsonValue], provider: VodProvider) -> str:
    if video.get('Url'):
        return string(video['Url'])
    filename = video.get('FileName')
    if video.get('Vid'):
        result = await provider.call(VodCall('GetMediaInfos', {'Vids': string(video['Vid'])}))
        entries = sequence(result.get('MediaInfoList'))
        if not entries:
            raise GenerationError('aideo_result_media_missing', 502)
        filename = mapping(mapping(entries[0]).get('SourceInfo')).get('FileName')
    config = provider.config
    if not filename or not config.playback_base or not config.playback_key:
        raise GenerationError('aideo_playback_configuration_required', 409)
    path = '/' + string(filename).lstrip('/')
    parsed = urlsplit(path)
    if parsed.query or parsed.fragment:
        raise GenerationError('aideo_filename_invalid', 502)
    stamp, nonce = str(int(time.time()) + 3600), secrets.token_hex(5)
    digest = hashlib.md5(f'{path}-{stamp}-{nonce}-0-{config.playback_key}'.encode()).hexdigest()
    return config.playback_base.rstrip('/') + path + f'?auth_key={stamp}-{nonce}-0-{digest}'


async def archive(outputs: Path, job: OwnedVideoJob, videos: list[dict[str, JsonValue]], provider: VodProvider) -> tuple[GeneratedAsset, ...]:
    directory = outputs / ('AI视频-' + job.id)
    if directory.is_symlink():
        raise GenerationError('aideo_output_path_invalid', 409)
    if directory.exists():
        project_identity(outputs, directory.name, job.persona)
    else:
        directory.mkdir()
    if (directory / '.easel.json').is_symlink():
        raise GenerationError('aideo_output_path_invalid', 409)
    write_json(directory / '.easel.json', {'profile': job.persona, 'generation_id': job.id, 'source': 'aideo'})
    assets: list[GeneratedAsset] = []
    for index, video in enumerate(videos):
        url = await resolve_video(video, provider)
        with tempfile.NamedTemporaryFile(dir=directory, suffix='.mp4', delete=False) as handle:
            temporary = Path(handle.name)
        try:
            await anyio.to_thread.run_sync(lambda: download_to_file(url, temporary, max_bytes=2 * 1024**3, timeout=120))
            await verify_video(temporary)
            digest = await anyio.to_thread.run_sync(NativeVideoProvider.hash_file, temporary)
            destination = directory / f'{index}.mp4'
            temporary.replace(destination)
            asset = GeneratedAsset(path=destination.relative_to(outputs).as_posix(), size=destination.stat().st_size, sha256=digest, kind='video')
            assets.append(asset)
            video['url'] = '/api/media/' + quote(asset.path, safe='/')
            if 'Url' in video:
                video['Url'] = video['url']
        finally:
            temporary.unlink(missing_ok=True)
    return tuple(assets)
