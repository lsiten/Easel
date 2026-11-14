"""Transactional persona-scoped account groups and account ordering."""
from __future__ import annotations

import json
import secrets

from easel.channels.models import ChannelError, JsonDocument
from easel.channels.store import CredentialStore


class AccountGroups:
    def __init__(self, store: CredentialStore) -> None:
        self.store = store
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS account_groups(persona TEXT NOT NULL, id TEXT NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(persona,id));
                CREATE TABLE IF NOT EXISTS account_group_members(persona TEXT NOT NULL, platform TEXT NOT NULL,
                    account_uid TEXT NOT NULL, group_id TEXT NOT NULL, rank REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(persona,platform,account_uid));
            ''')

    def list(self, persona: str) -> list[JsonDocument]:
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO account_groups VALUES(?,?,?)', (persona, 'default', json.dumps({'name': '默认分组', 'rank': 0})))
            db.execute('''INSERT OR IGNORE INTO account_group_members(persona,platform,account_uid,group_id)
                SELECT persona,platform,account_uid,'default' FROM credentials WHERE persona=?''', (persona,))
            db.execute('''DELETE FROM account_group_members WHERE persona=? AND NOT EXISTS(
                SELECT 1 FROM credentials c WHERE c.persona=account_group_members.persona
                AND c.platform=account_group_members.platform AND c.account_uid=account_group_members.account_uid)''', (persona,))
            rows = db.execute('SELECT id,data FROM account_groups WHERE persona=?', (persona,)).fetchall()
            members = db.execute('SELECT platform,account_uid,group_id,rank FROM account_group_members WHERE persona=? ORDER BY rank,platform,account_uid', (persona,)).fetchall()
        groups = [{'id': row['id'], 'is_default': row['id'] == 'default', **json.loads(row['data']),
                   'accounts': [{'platform': member['platform'], 'account_uid': member['account_uid'], 'rank': member['rank']}
                                for member in members if member['group_id'] == row['id']]} for row in rows]
        return sorted(groups, key=lambda group: (group.get('rank', 0), group['id']))

    def save(self, persona: str, data: JsonDocument, group_id: str = '') -> JsonDocument:
        self.list(persona)
        updating = bool(group_id)
        group_id = group_id or secrets.token_urlsafe(12)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM account_groups WHERE persona=? AND id=?', (persona, group_id)).fetchone()
            if updating and row is None: raise ChannelError('accounts', 'group_not_found', status=404)
            if not updating and not data.get('name'): raise ChannelError('accounts', 'group_name_required')
            previous = json.loads(row['data']) if row else {}
            updated = {**previous, **data}
            db.execute('INSERT INTO account_groups VALUES(?,?,?) ON CONFLICT(persona,id) DO UPDATE SET data=excluded.data',
                       (persona, group_id, json.dumps(updated)))
        return {'id': group_id, 'is_default': group_id == 'default', **updated}

    def update(self, persona: str, group_id: str, data: JsonDocument) -> JsonDocument:
        return self.save(persona, data, group_id)

    def delete(self, persona: str, ids: list[str]) -> None:
        self.list(persona)
        if not ids or len(set(ids)) != len(ids) or 'default' in ids:
            raise ChannelError('accounts', 'group_delete_invalid')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for group_id in ids:
                row = db.execute('SELECT 1 FROM account_groups WHERE persona=? AND id=?', (persona, group_id)).fetchone()
                if row is None: raise ChannelError('accounts', 'group_not_found', status=404)
            for group_id in ids:
                db.execute("UPDATE account_group_members SET group_id='default' WHERE persona=? AND group_id=?", (persona, group_id))
                db.execute('DELETE FROM account_groups WHERE persona=? AND id=?', (persona, group_id))

    def assign(self, persona: str, group_id: str, platform: str, account_uid: str) -> None:
        self.list(persona)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM account_groups WHERE persona=? AND id=?', (persona, group_id)).fetchone() is None:
                raise ChannelError('accounts', 'group_not_found', status=404)
            result = db.execute('UPDATE account_group_members SET group_id=? WHERE persona=? AND platform=? AND account_uid=?',
                                (group_id, persona, platform, account_uid))
            if result.rowcount != 1: raise ChannelError(platform, 'account_not_connected', status=404)

    def rank(self, persona: str, group_id: str, values: list[JsonDocument]) -> None:
        self.list(persona)
        identities = [(value['platform'], value['account_uid']) for value in values]
        if len(set(identities)) != len(identities): raise ChannelError('accounts', 'rank_duplicate_account')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM account_groups WHERE persona=? AND id=?', (persona, group_id)).fetchone() is None:
                raise ChannelError('accounts', 'group_not_found', status=404)
            for value in values:
                result = db.execute('UPDATE account_group_members SET rank=? WHERE persona=? AND group_id=? AND platform=? AND account_uid=?',
                    (value['rank'], persona, group_id, value['platform'], value['account_uid']))
                if result.rowcount != 1: raise ChannelError('accounts', 'group_account_not_found', status=404)
