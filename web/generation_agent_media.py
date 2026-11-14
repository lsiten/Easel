"""Stage structured Agent prompt media through the existing session attachment boundary."""
from __future__ import annotations
import base64
import binascii
from pathlib import Path
import shutil
from urllib.parse import urlsplit
import uuid
from typing import Literal

from PIL import Image
from typing_extensions import assert_never

from easel.channels.media_metadata import inspect_local_media
from easel.generation_agent_models import (AgentRun, ContentSource, DocumentBlock, ImageBlock,
    TextBlock, TextSource, URLSource, VideoBlock)
from easel.generation_inputs import local_image_request
from easel.generation_models import GenerationError, ImageRequest
from easel.ownership import owned_media_path
from easel.remote_media import RemoteMediaError, download_to_file
from web.server_binding import server


def session_id(run: AgentRun) -> str:
    return run.session_id or 'ai-agent-' + run.task_id


def stage_prompt(run: AgentRun, origin: str) -> tuple[str, list[server.AttachmentRef]]:
    attachments: list[server.AttachmentRef] = []
    def media(value: str, kind: Literal['image', 'video', 'document']) -> str:
        scope = server._attachment_scope(session_id(run), run.persona)
        directory = server._safe_output_target(f'_inbox/{scope}/{uuid.uuid4().hex}', must_exist=False)
        directory.mkdir(parents=True)
        temporary = directory / 'source.bin'
        try:
            mapped = local_image_request(ImageRequest(persona=run.persona, request_key=run.turn_id,
                prompt='Agent media', model='attachment', operation='edit', image=value), origin).images[0]
            if value.lower().startswith('data:'):
                header, _, content = value.partition(',')
                if not header.lower().endswith(';base64'):
                    raise GenerationError('agent_media_encoding_invalid', 422)
                decoded = base64.b64decode(content, validate=True)
                temporary.write_bytes(decoded)
            elif mapped != value:
                _, source = owned_media_path(server.OUTPUTS_DIR, run.persona, mapped)
                if source.stat().st_size > server.MAX_UPLOAD_MB * 1024**2:
                    raise GenerationError('agent_media_too_large', 413)
                shutil.copyfile(source, temporary)
            else:
                download_to_file(value, temporary, max_bytes=server.MAX_UPLOAD_MB * 1024**2, timeout=120)
            if temporary.stat().st_size > server.MAX_UPLOAD_MB * 1024**2:
                raise GenerationError('agent_media_too_large', 413)
            match kind:
                case 'image':
                    with Image.open(temporary) as picture:
                        suffix = (picture.format or 'png').lower()
                        picture.verify()
                case 'video':
                    metadata = inspect_local_media(temporary)
                    if not metadata.width or not metadata.height or not metadata.duration_sec:
                        raise GenerationError('agent_video_invalid', 422)
                    suffix = Path(urlsplit(value).path).suffix.removeprefix('.').lower()
                    suffix = suffix if suffix in {'mp4', 'webm', 'mov', 'm4v'} else 'mp4'
                case 'document':
                    with temporary.open('rb') as stream:
                        if not stream.read(5).startswith(b'%PDF-'):
                            raise GenerationError('agent_document_invalid', 422)
                    suffix = 'pdf'
                case unreachable:
                    assert_never(unreachable)
            destination = directory / ('attachment.' + suffix)
            temporary.replace(destination)
            destination.chmod(0o400)
            relative = destination.relative_to(server.OUTPUTS_DIR.resolve()).as_posix()
            attachments.append(server.AttachmentRef(id=server._attachment_id(scope, relative),
                                                    name=destination.name, path=relative))
            return f'[{kind} attachment {len(attachments)}]'
        except (RemoteMediaError, binascii.Error, OSError, Image.DecompressionBombError) as error:
            raise GenerationError('agent_media_invalid', 422) from error

    def block(item: TextBlock | ImageBlock | VideoBlock | DocumentBlock) -> str:
        match item:
            case TextBlock():
                return item.text
            case ImageBlock():
                return media(str(item.source.url), 'image')
            case VideoBlock():
                return media(str(item.source.url), 'video')
            case DocumentBlock():
                match item.source:
                    case URLSource():
                        return media(str(item.source.url), 'document')
                    case TextSource():
                        return item.source.data
                    case ContentSource():
                        content = item.source.content
                        return content if isinstance(content, str) else '\n'.join(block(child) for child in content)
                    case unreachable:
                        assert_never(unreachable)
            case unreachable:
                assert_never(unreachable)
    text = run.prompt if isinstance(run.prompt, str) else '\n'.join(block(item) for item in run.prompt)
    return text, attachments
