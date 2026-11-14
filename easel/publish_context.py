"""Validate and apply profile-owned publication source attribution."""
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from easel.content_library import ContentLibrary
from easel.content_library_models import ContentError, ContentScope, UsageInput


class PublishContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', populate_by_name=True)
    material_id: str = Field(default='', alias='materialId')
    material_group_id: str = Field(default='', alias='materialGroupId')
    media_ids: tuple[str, ...] = Field(default=(), alias='mediaIds')
    external_task_id: str = Field(default='', alias='taskId')
    source: str = ''


def validate_context(outputs: Path, persona: str, context: PublishContext) -> None:
    if not (context.material_id or context.material_group_id or context.media_ids):
        return
    library = ContentLibrary(outputs)
    scope = ContentScope(persona=persona, domain='material')
    if context.material_group_id:
        library.get_group(scope, context.material_group_id)
    if context.material_id:
        record = library.get(scope, context.material_id)
        if context.material_group_id and record.group_id != context.material_group_id:
            raise ContentError('publication_material_group_mismatch', 403)
    for identifier in context.media_ids:
        library.get(scope.model_copy(update={'domain': 'media'}), identifier)


def apply_usage(outputs: Path, persona: str, run_id: str, context: PublishContext) -> None:
    library = ContentLibrary(outputs)
    for domain, identifiers in (('material', (context.material_id,) if context.material_id else ()), ('media', context.media_ids)):
        scope = ContentScope(persona=persona, domain=domain)
        for identifier in identifiers:
            try:
                library.increment_usage(scope, UsageInput(ids=(identifier,), run_id=run_id))
            except ContentError as error:
                if error.status != 404:
                    raise
