"""Atomic media and draft record operations over the local content database."""
from __future__ import annotations

import sqlite3
from typing import Final
from typing_extensions import assert_never
import uuid

from easel.content_library_models import (ContentError, ContentFilter, ContentRecord, ContentScope,
    RecordInput, RecordPage, TransferInput, UsageInput)
from easel.content_library_store import ContentDatabase, content_now
from easel.ownership import OwnershipError, owned_media_path

VIDEO_PLATFORMS: Final = frozenset({'youtube', 'bilibili', 'kuaishou', 'tiktok', 'weixin-channels'})


class ContentRecords(ContentDatabase):
    def _validate_input(self, db: sqlite3.Connection, scope: ContentScope, payload: RecordInput) -> None:
        self.owner(scope)
        group = self.group(db, scope, payload.group_id)
        if group.kind is not None and group.kind != payload.kind:
            raise ContentError('group_content_kind_mismatch')
        paths = [payload.path, payload.thumbnail_path]
        for item in payload.media:
            paths.extend((item.path, item.thumbnail_path))
            if item.media_id:
                self.record(db, scope.model_copy(update={'domain': 'media'}), item.media_id)
        match scope.domain:
            case 'media':
                if not payload.path:
                    raise ContentError('media_path_required')
                if payload.material_id:
                    self.record(db, scope.model_copy(update={'domain': 'material'}), payload.material_id)
            case 'material':
                if not payload.title.strip():
                    raise ContentError('material_title_required')
            case unreachable:
                assert_never(unreachable)
        try:
            for path in filter(None, paths):
                owned_media_path(self.root, scope.persona, path)
        except OwnershipError as error:
            raise ContentError('media_ownership_invalid', 403) from error

    def create(self, scope: ContentScope, payload: RecordInput) -> ContentRecord:
        stamp = content_now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._validate_input(db, scope, payload)
            record = ContentRecord(**payload.model_dump(), id=uuid.uuid4().hex, persona=scope.persona,
                                   domain=scope.domain, created_at=stamp, updated_at=stamp)
            self.save_record(db, record)
        return record

    def get(self, scope: ContentScope, identifier: str) -> ContentRecord:
        self.owner(scope)
        with self.connect() as db:
            return self.record(db, scope, identifier)

    def update(self, scope: ContentScope, identifier: str, payload: RecordInput) -> ContentRecord:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self.record(db, scope, identifier)
            self._validate_input(db, scope, payload)
            record = old.model_copy(update={**payload.model_dump(), 'updated_at': content_now()})
            self.save_record(db, record)
        return record

    def _filtered(self, db: sqlite3.Connection, scope: ContentScope, query: ContentFilter) -> list[ContentRecord]:
        self.owner(scope)
        rows = db.execute('SELECT body FROM content_records WHERE persona=? AND domain=? ORDER BY rowid DESC',
                          (scope.persona, scope.domain))
        records = [ContentRecord.model_validate_json(row['body']) for row in rows]
        material_ids: set[str] = set()
        if query.material_group_id:
            material_ids = {row['id'] for row in db.execute('''SELECT id FROM content_records
                WHERE persona=? AND domain='material' AND group_id=?''', (scope.persona, query.material_group_id))}
        return [record for record in records
                if query.title.casefold() in record.title.casefold()
                and (not query.group_id or record.group_id == query.group_id)
                and (query.kind is None or record.kind == query.kind)
                and (query.status is None or record.status == query.status)
                and record.use_count >= query.min_use_count
                and (not query.material_group_id or record.material_id in material_ids)]

    def list_records(self, scope: ContentScope, query: ContentFilter) -> RecordPage:
        with self.connect() as db:
            records = self._filtered(db, scope, query)
        start = (query.page - 1) * query.page_size
        return RecordPage(items=tuple(records[start:start + query.page_size]), total=len(records),
                          page=query.page, page_size=query.page_size)

    def delete_ids(self, scope: ContentScope, identifiers: tuple[str, ...]) -> int:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            records = [self.record(db, scope, identifier) for identifier in dict.fromkeys(identifiers)]
            for record in records:
                db.execute('DELETE FROM content_records WHERE id=?', (record.id,))
        return len(records)

    def delete_one(self, scope: ContentScope, identifier: str) -> int:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record = self.record(db, scope, identifier)
            if record.domain == 'material' and record.auto_delete_media:
                for item in record.media:
                    if item.media_id:
                        db.execute("DELETE FROM content_records WHERE id=? AND persona=? AND domain='media'",
                                   (item.media_id, scope.persona))
            db.execute('DELETE FROM content_records WHERE id=?', (identifier,))
        return 1

    def delete_filter(self, scope: ContentScope, query: ContentFilter) -> int:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            records = self._filtered(db, scope, query)
            for record in records:
                db.execute('DELETE FROM content_records WHERE id=?', (record.id,))
        return len(records)

    def transfer(self, scope: ContentScope, payload: TransferInput) -> tuple[ContentRecord, ...]:
        results = []
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            group = self.group(db, scope, payload.target_group_id)
            records = [self.record(db, scope, identifier) for identifier in dict.fromkeys(payload.ids)]
            for old in records:
                if group.kind is not None and group.kind != old.kind:
                    raise ContentError('group_content_kind_mismatch')
                updates = {'group_id': group.id, 'updated_at': content_now()}
                match payload.mode:
                    case 'copy':
                        updates.update({'id': uuid.uuid4().hex, 'use_count': 0, 'created_at': content_now()})
                    case 'move':
                        pass
                    case unreachable:
                        assert_never(unreachable)
                record = old.model_copy(update=updates)
                self.save_record(db, record)
                results.append(record)
        return tuple(results)

    def increment_usage(self, scope: ContentScope, payload: UsageInput) -> tuple[ContentRecord, ...]:
        results = []
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            records = [self.record(db, scope, identifier) for identifier in dict.fromkeys(payload.ids)]
            for old in records:
                inserted = db.execute('INSERT OR IGNORE INTO content_usage VALUES(?,?,?)',
                                      (scope.persona, old.id, payload.run_id)).rowcount
                record = old.model_copy(update={'use_count': old.use_count + inserted, 'updated_at': content_now()})
                self.save_record(db, record)
                results.append(record)
        return tuple(results)

    def optimal(self, scope: ContentScope, group_id: str, platform: str) -> ContentRecord | None:
        if scope.domain != 'material':
            raise ContentError('optimal_requires_material')
        with self.connect() as db:
            group = self.group(db, scope, group_id)
            if group.platforms and platform not in group.platforms:
                return None
            records = self._filtered(db, scope, ContentFilter(group_id=group_id, status='success'))
            eligible = [record for record in records
                        if (platform not in VIDEO_PLATFORMS or record.kind == 'video')
                        and (not record.account_types or platform in record.account_types)
                        and (record.max_use_count is None or record.use_count < record.max_use_count)]
            return min(eligible, key=lambda record: (record.use_count, record.created_at, record.id), default=None)
