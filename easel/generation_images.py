"""Run image creation through Easel's existing image CLI and owned library."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote

import anyio
from pydantic import ValidationError
from PIL import Image

from easel.generation_models import (GeneratedAsset, GenerationError, GenerationJob,
                                     ImageReceipt, ImageRequest, ImageResult, ImageResultItem)
from easel.generation_catalog import runtime_image_model, image_channel
from easel.generation_gemini import native_ready, generate_native
from easel.generation_inputs import local_image_request, prepare_image_inputs
from easel.content_library_models import ContentError
from easel.generation_store import GenerationStore
from easel.harnesses.store import write_json
from easel.ownership import OwnershipError, owned_media_path
from easel.publish_recovery import worker_owner
from skills.shared.scripts.model_registry import configured_providers
from skills.shared.scripts.image_provider_config import ENV_ALIASES, detect_mode, detect_provider


class ImageGeneration:
    def __init__(self, outputs: Path, project: Path, settings: Mapping[str, str]) -> None:
        self.store = GenerationStore(outputs)
        self.project = project
        self.settings = settings

    def submit(self, payload: ImageRequest, origin: str = '') -> GenerationJob:
        request = local_image_request(payload, origin)
        existing = self.store.replay(request)
        if existing is not None:
            return existing
        channel = image_channel(self.store.outputs, request, self.settings)
        if not (native_ready(self.settings) if channel == 'gemini' else configured_providers('image', dict(self.settings))):
            raise GenerationError('generation_configuration_required', 409)
        base = next((self.settings.get(key, '').strip() for key in ('IMG_BASE_URL', *ENV_ALIASES['IMG_BASE_URL'])
                     if self.settings.get(key, '').strip()), '')
        if request.mask and (detect_mode(base, None) == 'async' or detect_provider(base) == 'agnes'):
            raise GenerationError('generation_mask_unsupported')
        return self.store.enqueue(request, runtime_image_model(self.store.outputs, request), channel)

    async def execute(self, identifier: str = '') -> GenerationJob | None:
        owner = worker_owner()
        job = self.store.claim(owner, identifier)
        if job is None:
            return None
        try:
            with anyio.fail_after(900):
                result = await self._generate(job, owner)
        except GenerationError as error:
            return self.store.finish(job, owner, 'failed', error=error.code)
        except OwnershipError:
            return self.store.finish(job, owner, 'failed', error='generation_input_changed')
        except ContentError as error:
            return self.store.finish(job, owner, 'failed', error=error.code)
        except TimeoutError:
            return self.store.finish(job, owner, 'unknown', error='generation_provider_timeout')
        except (OSError, SyntaxError, ValidationError, Image.DecompressionBombError):
            return self.store.finish(job, owner, 'failed', error='generation_artifact_invalid')
        except anyio.get_cancelled_exc_class():
            self.store.finish(job, owner, 'unknown', error='generation_worker_interrupted')
            raise
        assets = tuple(GeneratedAsset(path=item.path, sha256=item.sha256, size=item.size) for item in result.images)
        return self.store.finish(job, owner, 'succeeded', assets=assets, response=result)

    async def _generate(self, job: GenerationJob, owner: str) -> ImageResult:
        request = job.request
        if not (native_ready(self.settings) if job.channel == 'gemini' else configured_providers('image', dict(self.settings))):
            raise GenerationError('generation_configuration_required', 409)
        inputs, imported = await anyio.to_thread.run_sync(prepare_image_inputs, self.store.outputs, job)
        if imported:
            self.store.record_inputs(job, owner, imported)
        destination = self.store.outputs / f'AI图片-{job.id}'
        destination.mkdir()
        command = [sys.executable, str(self.project / 'skills/shared/scripts/ai_image.py'),
                   'img2img' if request.image else 'text2img', '--prompt=' + request.prompt,
                   '--n=' + str(request.n), '--size=' + request.size, '--output=' + str(destination / 'generated.png')]
        if inputs:
            command.extend(('--image', str(inputs[0])))
            for path in inputs[1:len(request.images)]:
                command.extend(('--reference-image', str(path)))
            if request.mask:
                command.extend(('--mask', str(inputs[-1])))
        for flag, value in (('quality', request.quality), ('style', request.style),
                            ('response-format', request.response_format), ('user', request.user)):
            if value:
                command.append('--' + flag + '=' + value)
        receipt_path = self.store.path.parent / 'receipts' / f'{job.id}.json'
        environment = {**os.environ, **self.settings, 'IMG_MODEL': job.runtime_model or request.model,
                       'EASEL_IMAGE_RECEIPT': str(receipt_path),
                       'EASEL_ROOT': str(self.store.outputs.parent)}
        if job.channel == 'gemini':
            await generate_native(job, tuple(inputs), self.settings, destination, receipt_path)
        else:
            with anyio.fail_after(900):
                try:
                    process = await anyio.run_process(command, env=environment, cwd=self.project,
                                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                except ValueError as error:
                    raise GenerationError('generation_process_configuration_invalid') from error
            if process.returncode != 0:
                raise GenerationError('generation_provider_failed')
        assets = []
        for path in sorted(destination.glob('generated*')):
            if path.is_symlink() or not path.is_file() or path.resolve().parent != destination.resolve():
                raise GenerationError('generation_artifact_invalid')
            relative = path.relative_to(self.store.outputs).as_posix()
            with Image.open(path) as picture:
                picture.verify()
            digest = self._hash(path)
            assets.append(GeneratedAsset(path=relative, size=path.stat().st_size, sha256=digest))
        if not assets:
            raise GenerationError('generation_result_empty')
        write_json(destination / '.easel.json', {'profile': job.persona, 'generation_id': job.id, 'source': 'ai-image'})
        for asset in assets:
            owned_media_path(self.store.outputs, job.persona, asset.path)
        receipt = ImageReceipt.model_validate_json(receipt_path.read_bytes()) if receipt_path.is_file() else ImageReceipt(created=job.created_at)
        items = tuple(ImageResultItem(path=asset.path, sha256=asset.sha256, size=asset.size,
            url='/api/media/' + quote(asset.path, safe='/'),
            revised_prompt=receipt.revised_prompts[index] if index < len(receipt.revised_prompts) else '')
            for index, asset in enumerate(assets))
        return ImageResult(created=receipt.created, images=items, usage=receipt.usage, model=receipt.model, provider=receipt.provider,
                           background=receipt.background, output_format=receipt.output_format,
                           quality=receipt.quality, size=receipt.size)

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest()


async def dispatch_images(factory: Callable[[], ImageGeneration]) -> None:
    from easel.generation_dispatch import dispatch_pool
    from easel.generation_queue import QueuePolicy
    service = factory()

    async def execute() -> bool:
        return await factory().execute() is not None

    await dispatch_pool(execute, service.store.recover, QueuePolicy('image', service.store.outputs))
