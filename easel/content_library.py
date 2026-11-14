"""Persona-owned content groups with separate media and material domains."""
from __future__ import annotations

import uuid

from easel.content_library_models import (ContentError, ContentFilter, ContentGroup, ContentScope,
    GroupInput, GroupPage, GroupView)
from easel.content_library_store import content_now
from easel.content_library_records import ContentRecords


class ContentLibrary(ContentRecords):
    def create_group(self, scope: ContentScope, payload: GroupInput) -> ContentGroup:
        self.owner(scope)
        group = ContentGroup(**payload.model_dump(), id=uuid.uuid4().hex, persona=scope.persona,
                             domain=scope.domain, created_at=content_now())
        with self.connect() as db:
            db.execute('INSERT INTO content_groups VALUES(?,?,?,?,?)',
                       (group.id, scope.persona, scope.domain, 0, group.model_dump_json()))
        return group

    def get_group(self, scope: ContentScope, identifier: str) -> ContentGroup:
        self.owner(scope)
        with self.connect() as db:
            return self.group(db, scope, identifier)

    def update_group(self, scope: ContentScope, identifier: str, payload: GroupInput) -> ContentGroup:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self.group(db, scope, identifier)
            group = old.model_copy(update=payload.model_dump(exclude_unset=True))
            if old.kind != group.kind:
                raise ContentError('group_kind_immutable')
            db.execute('UPDATE content_groups SET body=? WHERE id=?', (group.model_dump_json(), identifier))
        return group

    def delete_group(self, scope: ContentScope, identifier: str) -> None:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            group = self.group(db, scope, identifier)
            if group.is_default:
                raise ContentError('default_group_protected', 409)
            default = self.ensure_default(db, scope)
            for row in db.execute('SELECT body FROM content_records WHERE group_id=?', (identifier,)).fetchall():
                from easel.content_library_models import ContentRecord
                record = ContentRecord.model_validate_json(row['body'])
                self.save_record(db, record.model_copy(update={'group_id': default.id, 'updated_at': content_now()}))
            db.execute('DELETE FROM content_groups WHERE id=?', (identifier,))

    def list_groups(self, scope: ContentScope, query: ContentFilter) -> GroupPage:
        with self.connect() as db:
            self.ensure_default(db, scope)
            groups = [ContentGroup.model_validate_json(row['body']) for row in db.execute(
                'SELECT body FROM content_groups WHERE persona=? AND domain=? ORDER BY is_default DESC,rowid',
                (scope.persona, scope.domain))]
            groups = [group for group in groups if query.title.casefold() in group.name.casefold()]
            start = (query.page - 1) * query.page_size
            views = []
            for group in groups[start:start + query.page_size]:
                records = self._filtered(db, scope, ContentFilter(group_id=group.id))
                thumbnails = tuple(record.thumbnail_path or record.path for record in records[:3]
                                   if record.thumbnail_path or record.path)
                views.append(GroupView(**group.model_dump(), thumbnails=thumbnails))
            return GroupPage(items=tuple(views), total=len(groups), page=query.page, page_size=query.page_size)
