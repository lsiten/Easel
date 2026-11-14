"""Archive returned Ark frames alongside the validated owned video."""
from pathlib import Path
import tempfile
from urllib.parse import quote

import anyio
from PIL import Image

from easel.generation_models import GeneratedAsset, GenerationError
from easel.generation_video_models import VideoJob
from easel.generation_video_provider import NativeVideoProvider
from easel.remote_media import download_to_file


async def archive_last_frame(outputs: Path, job: VideoJob) -> tuple[GeneratedAsset, ...]:
    response = job.response
    native = response.volcengine if response else None
    content = native.content if native else None
    remote_image = content.last_frame_url if content else response.relay.coverUrl if response and response.relay else ''
    if not remote_image:
        return ()
    directory = outputs / ('AI视频-' + job.id)
    if directory.is_symlink():
        raise GenerationError('video_output_path_invalid')
    directory.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, suffix='.image', delete=False) as stream:
        temporary = Path(stream.name)
    try:
        await anyio.to_thread.run_sync(lambda: download_to_file(remote_image, temporary,
                                                                max_bytes=50 * 1024**2, timeout=120))
        def verify() -> str:
            with Image.open(temporary) as picture:
                suffix = (picture.format or '').lower()
                picture.verify()
            if suffix not in {'png', 'jpeg', 'webp', 'gif', 'bmp', 'tiff'}:
                raise GenerationError('video_frame_format_invalid')
            return suffix
        suffix = await anyio.to_thread.run_sync(verify)
        digest = await anyio.to_thread.run_sync(NativeVideoProvider.hash_file, temporary)
        destination = directory / (('last-frame.' if content else 'cover.') + suffix)
        temporary.replace(destination)
        return (GeneratedAsset(path=destination.relative_to(outputs).as_posix(), size=destination.stat().st_size,
                               sha256=digest, kind='image'),)
    finally:
        temporary.unlink(missing_ok=True)


def localize_volcengine_result(job: VideoJob, video: GeneratedAsset) -> VideoJob:
    response = job.response
    native = response.volcengine if response else None
    if response is None or native is None or native.content is None:
        return job
    video_url = '/api/media/' + quote(video.path, safe='/')
    frame_url = '/api/media/' + quote(job.additional_assets[0].path, safe='/') if job.additional_assets else None
    content = native.content.model_copy(update={'video_url': video_url, 'last_frame_url': frame_url})
    return job.model_copy(update={'response': response.model_copy(update={'video_url': video_url,
        'volcengine': native.model_copy(update={'content': content})})})
