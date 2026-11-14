"""HTTP entry points for persona-scoped local content library operations."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from easel.content_library import ContentLibrary
from easel.content_library_models import (ContentError, ContentFilter, ContentScope, Domain,
    GroupInput, IdSelection, RecordInput, RemoteImportInput, TransferInput, UsageInput)


@contextmanager
def content_errors() -> Iterator[None]:
    try:
        yield
    except ContentError as error:
        raise HTTPException(error.status, error.code) from error


def create_router(root: Callable[[], Path], profile_valid: Callable[[str], bool],
                  require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter(prefix='/api/content', tags=['content'])

    def scope(domain: Domain, persona: str) -> ContentScope:
        if not profile_valid(persona):
            raise HTTPException(400, 'persona_invalid')
        return ContentScope(persona=persona, domain=domain)

    @router.post('/media/import-url')
    def import_url(persona: str, payload: RemoteImportInput, request: Request):
        require_write(request)
        from easel.content_library_import import RemoteContentImporter
        with content_errors():
            return RemoteContentImporter(ContentLibrary(root())).import_media(scope('media', persona), payload)

    @router.get('/{domain}/groups')
    def groups(domain: Domain, persona: str, query: ContentFilter = Depends()):
        with content_errors():
            return ContentLibrary(root()).list_groups(scope(domain, persona), query)

    @router.post('/{domain}/groups')
    def create_group(domain: Domain, persona: str, payload: GroupInput, request: Request):
        require_write(request)
        with content_errors():
            return ContentLibrary(root()).create_group(scope(domain, persona), payload)

    @router.get('/{domain}/groups/{group_id}')
    def get_group(domain: Domain, group_id: str, persona: str):
        with content_errors():
            return ContentLibrary(root()).get_group(scope(domain, persona), group_id)

    @router.put('/{domain}/groups/{group_id}')
    def update_group(domain: Domain, group_id: str, persona: str, payload: GroupInput, request: Request):
        require_write(request)
        with content_errors():
            return ContentLibrary(root()).update_group(scope(domain, persona), group_id, payload)

    @router.delete('/{domain}/groups/{group_id}')
    def delete_group(domain: Domain, group_id: str, persona: str, request: Request):
        require_write(request)
        with content_errors():
            ContentLibrary(root()).delete_group(scope(domain, persona), group_id)
            return {'deleted': True}

    @router.get('/{domain}/records')
    def records(domain: Domain, persona: str, query: ContentFilter = Depends()):
        with content_errors():
            return ContentLibrary(root()).list_records(scope(domain, persona), query)

    @router.post('/{domain}/records')
    def create_record(domain: Domain, persona: str, payload: RecordInput, request: Request):
        require_write(request)
        with content_errors():
            return ContentLibrary(root()).create(scope(domain, persona), payload)

    @router.delete('/{domain}/records/bulk')
    def delete_bulk(domain: Domain, persona: str, payload: IdSelection, request: Request):
        require_write(request)
        with content_errors():
            return {'count': ContentLibrary(root()).delete_ids(scope(domain, persona), payload.ids)}

    @router.delete('/{domain}/records/filter')
    def delete_filter(domain: Domain, persona: str, request: Request, query: ContentFilter = Depends()):
        require_write(request)
        with content_errors():
            return {'count': ContentLibrary(root()).delete_filter(scope(domain, persona), query)}

    @router.post('/{domain}/transfer')
    def transfer(domain: Domain, persona: str, payload: TransferInput, request: Request):
        require_write(request)
        with content_errors():
            return {'items': ContentLibrary(root()).transfer(scope(domain, persona), payload)}

    @router.post('/{domain}/usage')
    def usage(domain: Domain, persona: str, payload: UsageInput, request: Request):
        require_write(request)
        with content_errors():
            return {'items': ContentLibrary(root()).increment_usage(scope(domain, persona), payload)}

    @router.get('/{domain}/optimal')
    def optimal(domain: Domain, persona: str, group_id: str, platform: str):
        with content_errors():
            return ContentLibrary(root()).optimal(scope(domain, persona), group_id, platform)

    @router.get('/{domain}/records/{record_id}')
    def get_record(domain: Domain, record_id: str, persona: str):
        with content_errors():
            return ContentLibrary(root()).get(scope(domain, persona), record_id)

    @router.put('/{domain}/records/{record_id}')
    def update_record(domain: Domain, record_id: str, persona: str, payload: RecordInput, request: Request):
        require_write(request)
        with content_errors():
            return ContentLibrary(root()).update(scope(domain, persona), record_id, payload)

    @router.delete('/{domain}/records/{record_id}')
    def delete_record(domain: Domain, record_id: str, persona: str, request: Request):
        require_write(request)
        with content_errors():
            return {'count': ContentLibrary(root()).delete_one(scope(domain, persona), record_id)}

    return router
