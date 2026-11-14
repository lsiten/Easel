"""Native image/video execution and bounded video import for draft pipelines."""
from __future__ import annotations
import base64
from collections.abc import Mapping
from pathlib import Path
import subprocess
import sys
import anyio
from typing_extensions import assert_never
from easel.content_library import ContentLibrary
from easel.content_library_import import RemoteContentImporter
from easel.content_library_models import ContentScope, RemoteImportInput
from easel.generation_draft_models import DraftJob, DraftMedia
from easel.generation_models import GeneratedAsset, GenerationError, ImageRequest
from easel.generation_images import ImageGeneration
from easel.generation_videos import VideoGeneration
from easel.generation_video_request import VideoGenerationRequest
from easel.ownership import owned_media_path


async def imported_video(outputs: Path, job: DraftJob) -> DraftMedia:
    request = job.request
    if request.kind != 'video-url':
        raise GenerationError('draft_video_url_required')
    library = ContentLibrary(outputs)
    scope = ContentScope(persona=job.persona, domain='media')
    with library.connect() as db:
        group = library.ensure_default(db, scope)
    payload = RemoteImportInput(request_id='draft-video-'+job.id, group_id=group.id, url=str(request.videoUrl), kind='video')
    record = await anyio.to_thread.run_sync(RemoteContentImporter(library).import_media, scope, payload)
    _, path = owned_media_path(outputs, job.persona, record.path)
    digest = await anyio.to_thread.run_sync(ImageGeneration._hash, path)
    return DraftMedia(asset=GeneratedAsset(path=record.path, kind='video', sha256=digest, size=path.stat().st_size), generationId=record.id)


async def video_frames(outputs: Path, persona: str, relative: str) -> tuple[str, ...]:
    _, path = owned_media_path(outputs, persona, relative)
    folder = path.parent / (path.stem + '-draft-frames')
    if folder.is_symlink():
        raise GenerationError('draft_frame_path_unsafe')
    folder.mkdir(exist_ok=True)
    # Reuse the reference-analysis implementation in a bounded subprocess; no ASR model downloads.
    code = """from pathlib import Path
import sys
import subprocess
from skills.shared.scripts import ref_analysis
def bounded_run(command, **kwargs):
    return subprocess.run(command[:1]+['-protocol_whitelist','file,pipe']+command[1:], timeout=20, **kwargs)
ref_analysis._run = bounded_run
_probe, _extract_keyframes = ref_analysis._probe, ref_analysis._extract_keyframes
p, out = Path(sys.argv[1]), Path(sys.argv[2])
m = _probe(p)
duration = m['duration']
if not 0 < duration <= 14400 or not m['width'] or not m['height']:
    raise SystemExit(2)
scenes = [{'id': i, 'start': duration*i/6, 'end': duration*(i+1)/6, 'duration': duration/6} for i in range(6)]
frames = _extract_keyframes(p, scenes, 6, 'standard', out)
raise SystemExit(0 if frames else 3)
"""
    with anyio.fail_after(90):
        result = await anyio.run_process([sys.executable, '-c', code, str(path), str(folder)],
            cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise GenerationError('draft_video_analysis_failed', 502)
    frames = tuple(file.relative_to(outputs).as_posix() for file in sorted(folder.glob('kf_*.jpg')) if file.is_file() and not file.is_symlink())
    if not frames:
        raise GenerationError('draft_video_analysis_empty', 502)
    return frames


class NativeDraftMedia:
    def __init__(self, outputs: Path, project: Path, settings: Mapping[str, str]) -> None:
        self.outputs, self.project, self.settings = outputs, project, settings

    async def media(self, job: DraftJob, position: int) -> DraftMedia:
        if job.plan is None:
            raise GenerationError('draft_plan_missing')
        request = job.request
        match request.kind:
            case 'image-text':
                service = ImageGeneration(self.outputs, self.project, self.settings)
                size = request.imageSize or '1024x1024'
                if size in ('1K', '2K', '4K'):
                    pixels = {'1K': 1024, '2K': 2048, '4K': 4096}[size]
                    width, height = (int(value) for value in (request.aspectRatio or '1:1').split(':'))
                    size = f'{pixels if width >= height else round(pixels*width/height)}x{pixels if height >= width else round(pixels*height/width)}'
                elif request.aspectRatio and not request.imageSize:
                    width, height = (int(value) for value in request.aspectRatio.split(':'))
                    size = f'{1024 if width >= height else round(1024*width/height)}x{1024 if height >= width else round(1024*height/width)}'
                child = service.submit(ImageRequest(persona=job.persona, request_key=f'draft-{job.id}-image-{position}',
                    model=request.imageModel, prompt=job.plan.imagePrompts[position], size=size,
                    imageSize=request.imageSize if request.imageSize in ('0.5K', '1K', '2K', '4K') else '', aspectRatio=request.aspectRatio or '',
                    operation='edit' if request.imageUrls else 'generate', image=tuple(str(url) for url in request.imageUrls)))
                with anyio.fail_after(900):
                    while child.state in ('queued', 'running'):
                        await service.execute(child.id)
                        child = service.store.get(job.persona, child.id)
                        if child.state in ('queued', 'running'):
                            await anyio.sleep(0.25)
                if child.state != 'succeeded' or not child.assets:
                    raise GenerationError('draft_image_' + child.state, 502)
                return DraftMedia(asset=child.assets[0], generationId=child.id)
            case 'video':
                service = VideoGeneration(self.outputs, self.settings)
                child = service.submit_generic(VideoGenerationRequest(persona=job.persona, request_key='draft-'+job.id+'-video',
                    model=request.model, prompt=job.plan.videoPrompt,
                    image=str(request.imageUrls[0]) if len(request.imageUrls) == 1 else tuple(str(url) for url in request.imageUrls),
                    video_url=str(request.videoUrls[0]) if len(request.videoUrls) == 1 else '',
                    videos=tuple(str(url) for url in request.videoUrls) if len(request.videoUrls) > 1 else (), audios=tuple(str(url) for url in request.audioUrls),
                    resolution=request.resolution, duration=request.duration, ratio=request.aspectRatio or ''))
                with anyio.fail_after(900):
                    while child.state in ('queued', 'waiting', 'submitting'):
                        await service.execute(child.id)
                        child = service.store.get(job.persona, child.id)
                        if child.state in ('queued', 'waiting', 'submitting'):
                            await anyio.sleep(2)
                if child.state != 'succeeded' or child.asset is None:
                    raise GenerationError('draft_video_' + child.state, 502)
                frames = await video_frames(self.outputs, job.persona, child.asset.path)
                return DraftMedia(asset=child.asset, thumbnail=frames[0], generationId=child.id)
            case 'video-url':
                media = await imported_video(self.outputs, job)
                frames = await video_frames(self.outputs, job.persona, media.asset.path)
                return media.model_copy(update={'thumbnail': frames[0]})
            case unreachable:
                assert_never(unreachable)


def frame_data(outputs: Path, persona: str, relative: str) -> str:
    _, path = owned_media_path(outputs, persona, relative)
    if path.stat().st_size > 4 * 1024**2:
        raise GenerationError('draft_frame_too_large')
    return 'data:image/jpeg;base64,' + base64.b64encode(path.read_bytes()).decode()
