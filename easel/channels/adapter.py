"""Official API adapters with platform-specific staged publication semantics."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from collections.abc import Mapping

import httpx

from easel.channels.actions import comments, create_comment, delete_work, publish_options, update_work
from easel.channels.definitions import definition
from easel.channels.graph_publish import finalize_graph, publish_graph
from easel.channels.http import ProviderHttp
from easel.channels.engagement import assert_reply_target, delete_comment, engagement_action
from easel.channels.links import expand_short_link, link_identity
from easel.channels.facebook_upload import finalize_facebook
from easel.channels.youtube import apply_thumbnail
from easel.channels.store import CredentialStore
from easel.channels.native_definitions import NATIVE_IDS
from easel.channels.native_adapter import NativeAdapter
from easel.channels.models import AccountProfile, ChannelError, Credential, JsonDocument, PublishContent, PublishReceipt
from easel.channels.publish import finalize_tiktok, finalize_youtube, publish_simple, validate_content
from easel.channels.reads import ChannelReads, _document, _rows
from easel.channels.uploads import initialize_upload, media_status, upload_file


class ChannelAdapter:
    """Resolve one provider while leaving authorization, approval, and ledger ownership to the service."""

    def __init__(self, platform: str, *, transport: httpx.BaseTransport | None = None,
                 settings: Mapping[str, str] | None = None, store: CredentialStore | None = None) -> None:
        self.definition = definition(platform)
        self.reader = ChannelReads(self.definition, ProviderHttp(platform, transport=transport))
        self.settings = dict(settings or {})
        self.store = store

    def _scope(self, credential: Credential) -> None:
        if credential.platform != self.definition.platform:
            raise ChannelError(self.definition.platform, 'credential_platform_mismatch', status=403)
        if not credential.access_token.get_secret_value() and self.definition.auth_mode != 'app_handoff':
            raise ChannelError(self.definition.platform, 'access_token_missing', status=401)

    def profile(self, credential: Credential) -> AccountProfile:
        self._scope(credential)
        if self.definition.platform in NATIVE_IDS: return NativeAdapter(self).profile(credential)
        return self.reader.profile(credential)

    def accounts(self, credential: Credential) -> tuple[AccountProfile, ...]:
        self._scope(credential)
        if self.definition.platform in NATIVE_IDS: return (self.profile(credential),)
        return self.reader.selectable_accounts(credential)

    def select_account(self, credential: Credential, account_uid: str) -> Credential:
        """Select only an account returned by the authenticated provider."""
        self._scope(credential)
        if self.definition.platform != 'facebook':
            match = next((account for account in self.accounts(credential) if account.account_uid == account_uid), None)
            if match is None: raise ChannelError(self.definition.platform, 'account_not_selectable', status=403)
            return credential.model_copy(update={'account_uid': match.account_uid, 'display_name': match.display_name,
                'parent_uid': match.parent_uid or credential.parent_uid or credential.account_uid, 'profile': match})
        page = next((row for row in self.reader.facebook_pages(credential) if str(row.get('id')) == account_uid), None)
        if page is None or not isinstance(page.get('access_token'), str):
            raise ChannelError('facebook', 'page_not_selectable', status=403)
        from pydantic import SecretStr
        return credential.model_copy(update={'account_uid': account_uid, 'access_token': SecretStr(page['access_token']),
            'display_name': str(page.get('name') or ''), 'parent_uid': credential.account_uid,
            'profile': self.reader._profile(page, 'id', 'name').model_copy(update={'parent_uid': credential.account_uid})})

    def works(self, credential: Credential, cursor: str = '', *, limit: int = 25, direction: str = 'next') -> JsonDocument:
        self._scope(credential)
        if self.definition.platform in NATIVE_IDS: return NativeAdapter(self).works(credential, cursor, limit, direction)
        if len(cursor) > 1024: raise ChannelError(self.definition.platform, 'cursor_invalid')
        return self.reader.works(credential, cursor, limit=limit, direction=direction)

    def resolve_link(self, credential: Credential, link: str) -> JsonDocument:
        self._scope(credential)
        if self.definition.platform in NATIVE_IDS: return NativeAdapter(self).resolve_link(credential, link)
        identity, canonical = link_identity(self.definition.platform, expand_short_link(self.definition.platform, link))
        if not identity: raise ChannelError(self.definition.platform, 'work_link_invalid')
        if self.definition.platform in {'instagram', 'threads', 'facebook'}:
            cursor, seen = '', set()
            while True:
                payload = self.reader.works(credential, cursor)
                for row in _rows(payload.get('data')):
                    permalink = str(row.get('permalink') or row.get('permalink_url') or '').rstrip('/')
                    if permalink == canonical or str(row.get('id')) == identity:
                        identity = str(row['id'])
                        return {'platform_work_id': identity, 'permalink': permalink, 'work': self.work(credential, identity)}
                after = _document(_document(payload.get('paging')).get('cursors')).get('after')
                if not isinstance(after, str) or not after or after in seen: break
                seen.add(after)
                cursor = after
            raise ChannelError(self.definition.platform, 'work_owner_unverified', status=403)
        return {'platform_work_id': identity, 'permalink': canonical, 'work': self.work(credential, identity)}

    def work(self, credential: Credential, work_id: str) -> JsonDocument:
        self._scope(credential)
        if not work_id or len(work_id) > 512: raise ChannelError(self.definition.platform, 'work_id_invalid')
        if self.definition.platform in NATIVE_IDS: return NativeAdapter(self).work(credential, work_id)
        result = self.reader.work(credential, work_id)
        if self.definition.platform in {'instagram', 'facebook', 'threads', 'pinterest'}:
            author = _document(result.get('from')).get('id') or _document(result.get('owner')).get('id')
            if author is not None:
                if str(author) != credential.account_uid: raise ChannelError(self.definition.platform, 'work_owner_mismatch', status=403)
                return result
            username = result.get('username') or _document(result.get('board_owner')).get('username')
            if isinstance(username, str) and username:
                if self.definition.platform == 'pinterest':
                    profile = self.reader.call('GET', '/user_account', credential).data
                    owned = str(profile.get('id')) == credential.account_uid and profile.get('username') == username
                else:
                    profile = self.reader.profile(credential)
                    owned = profile.account_uid == credential.account_uid and (profile.username or profile.display_name) == username
                if not owned: raise ChannelError(self.definition.platform, 'work_owner_mismatch', status=403)
                return result
            self._assert_listed_owner(credential, work_id)
        return result

    def _assert_listed_owner(self, credential: Credential, work_id: str) -> None:
        cursor = ''
        seen: set[str] = set()
        while True:
            payload = self.reader.works(credential, cursor)
            rows = _rows(payload.get('items') or payload.get('data'))
            if any(str(row.get('id')) == work_id for row in rows): return
            next_cursor = payload.get('bookmark') or _document(_document(payload.get('paging')).get('cursors')).get('after')
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen: break
            seen.add(next_cursor)
            cursor = next_cursor
        raise ChannelError(self.definition.platform, 'work_owner_unverified', status=403)

    def analytics(self, credential: Credential, work_id: str = '', *, start_date: str = '', end_date: str = '') -> JsonDocument:
        self._scope(credential)
        if self.definition.platform in NATIVE_IDS: return NativeAdapter(self).analytics(credential, work_id)
        if work_id: self.work(credential, work_id)
        from datetime import date, timedelta
        if self.definition.platform == 'pinterest' and work_id and not start_date and not end_date:
            end_date = date.today().isoformat()
            start_date = (date.today() - timedelta(days=30)).isoformat()
        if start_date or end_date:
            try:
                start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
            except ValueError as error:
                raise ChannelError(self.definition.platform, 'analytics_date_range_invalid') from error
            if start > end: raise ChannelError(self.definition.platform, 'analytics_date_range_invalid')
        if self.definition.platform == 'pinterest':
            if not work_id:
                return self.reader.call('GET', '/user_account', credential).data
            return self.reader.call('GET', '/pins/' + quote(work_id, safe='') + '/analytics', credential,
                params={'start_date': start_date, 'end_date': end_date, 'metric_types': 'IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK'}).data
        return self.reader.analytics(credential, work_id, since=start_date, until=end_date)

    def options(self, credential: Credential, *, keyword: str = '', latitude: float | None = None, longitude: float | None = None) -> JsonDocument:
        self._scope(credential)
        if self.definition.platform in NATIVE_IDS: return NativeAdapter(self).options(credential)
        if self.definition.platform == 'threads':
            if not keyword and (latitude is None or longitude is None): return {'modes': list(self.definition.publish_modes), 'location_search_requires': 'keyword or latitude and longitude'}
            if len(keyword) > 200 or (latitude is not None and not -90 <= latitude <= 90) or (longitude is not None and not -180 <= longitude <= 180):
                raise ChannelError('threads', 'location_filter_invalid')
            params = {'fields': 'id,address,city,country,name,latitude,longitude,postal_code'}
            if keyword: params['q'] = keyword
            if latitude is not None: params['latitude'] = str(latitude)
            if longitude is not None: params['longitude'] = str(longitude)
            return self.reader.call('GET', '/location_search', credential, params=params).data
        return publish_options(self.reader, credential)

    def cancel(self, credential: Credential, receipt: PublishReceipt) -> JsonDocument:
        """Cancel only a receipt loaded from the caller's persona/account ledger."""
        self._scope(credential)
        identity = receipt.platform_work_id or receipt.external_request_id
        if not identity: raise ChannelError(self.definition.platform, 'publication_identity_required')
        if self.definition.platform == 'tiktok':
            return self.reader.call('POST', '/post/publish/cancel/', credential, body={'publish_id': receipt.external_request_id}).data
        if self.definition.platform == 'linkedin':
            return self.reader.call('DELETE', '/posts/' + quote(identity, safe=''), credential).data
        if self.definition.platform in {'instagram', 'facebook', 'threads', 'twitter', 'pinterest'}:
            edge = '/tweets/' if self.definition.platform == 'twitter' else '/pins/' if self.definition.platform == 'pinterest' else '/'
            return self.reader.call('DELETE', edge + quote(identity, safe=''), credential).data
        raise ChannelError(self.definition.platform, 'cancel_unsupported', status=405)

    def publish(self, credential: Credential, content: PublishContent, *, media_paths=(), cover_path: Path | None = None) -> PublishReceipt:
        self._scope(credential)
        validate_content(self.reader, content)
        if self.definition.platform in NATIVE_IDS: return NativeAdapter(self).publish(credential, content, media_paths=media_paths, cover_path=cover_path)
        if self.definition.platform == 'facebook' and not credential.parent_uid:
            raise ChannelError('facebook', 'page_account_required')
        if self.definition.platform in {'facebook', 'instagram', 'threads'}:
            return publish_graph(self.reader, credential, content)
        return publish_simple(self.reader, credential, content)

    def finalize(self, credential: Credential, receipt: PublishReceipt) -> PublishReceipt:
        self._scope(credential)
        if self.definition.platform in NATIVE_IDS: return NativeAdapter(self).finalize(credential, receipt)
        match self.definition.platform:
            case 'youtube': return finalize_youtube(self.reader, credential, receipt)
            case 'facebook': return finalize_facebook(self.reader, credential, receipt)
            case 'instagram' | 'threads': return finalize_graph(self.reader, credential, receipt)
            case 'tiktok': return finalize_tiktok(self.reader, credential, receipt)
            case 'linkedin' | 'pinterest' | 'twitter': return media_status(self.reader, credential, receipt)
            case _: raise ChannelError(self.definition.platform, 'finalize_unsupported', status=405)

    def initialize_upload(self, credential: Credential, path: Path, kind: str) -> PublishReceipt:
        self._scope(credential)
        if self.definition.platform in NATIVE_IDS: return NativeAdapter(self).initialize_upload(credential, path)
        return initialize_upload(self.reader, credential, path, kind)

    def upload(self, credential: Credential, receipt: PublishReceipt, path: Path) -> PublishReceipt:
        self._scope(credential)
        if self.definition.platform in NATIVE_IDS: return NativeAdapter(self).upload(credential, receipt, path)
        return upload_file(self.reader, credential, receipt, path)

    def apply_cover(self, credential: Credential, receipt: PublishReceipt, path: Path) -> PublishReceipt:
        self._scope(credential)
        if self.definition.platform != 'youtube': raise ChannelError(self.definition.platform, 'cover_stage_unsupported', status=405)
        return apply_thumbnail(self.reader, credential, receipt, path)

    def update(self, credential: Credential, work_id: str, content: PublishContent, *, expected_etag: str = '') -> JsonDocument:
        if self.definition.platform in NATIVE_IDS: raise ChannelError(self.definition.platform, 'update_unsupported', status=405)
        if len(expected_etag) > 2048 or any(ord(char) < 32 for char in expected_etag): raise ChannelError(self.definition.platform, 'etag_invalid')
        self.work(credential, work_id)
        return update_work(self.reader, credential, work_id, content, expected_etag=expected_etag)

    def delete(self, credential: Credential, work_id: str) -> None:
        if self.definition.platform in NATIVE_IDS: raise ChannelError(self.definition.platform, 'delete_unsupported', status=405)
        self.work(credential, work_id)
        delete_work(self.reader, credential, work_id)

    def comments(self, credential: Credential, work_id: str, cursor: str = '', *, limit: int = 25, direction: str = 'next') -> JsonDocument:
        self._scope(credential)
        if not work_id or len(work_id) > 512 or len(cursor) > 1024:
            raise ChannelError(self.definition.platform, 'engagement_target_invalid')
        return comments(self.reader, credential, work_id, cursor, limit=limit, direction=direction)

    def comment(self, credential: Credential, work_id: str, text: str, *, reply_to_id: str = '') -> JsonDocument:
        self._scope(credential)
        if not work_id or len(work_id) > 512 or len(text) > 10000:
            raise ChannelError(self.definition.platform, 'engagement_target_invalid')
        if self.definition.platform == 'threads': raise ChannelError('threads', 'comment_create_unsupported', status=405)
        if reply_to_id:
            if len(reply_to_id) > 512: raise ChannelError(self.definition.platform, 'reply_parent_invalid')
            assert_reply_target(self.reader, credential, work_id, reply_to_id)
            if self.definition.platform in {'youtube', 'instagram'}:
                return engagement_action(self.reader, credential, 'reply', reply_to_id, text=text)
            return create_comment(self.reader, credential, reply_to_id, text)
        return create_comment(self.reader, credential, work_id, text)

    def engage(self, credential: Credential, action: str, target_id: str, *, text: str = '', work_id: str = '') -> JsonDocument:
        self._scope(credential)
        if len(text) > 10000: raise ChannelError(self.definition.platform, 'comment_too_long')
        if action == 'reply':
            if not work_id: raise ChannelError(self.definition.platform, 'reply_work_required')
            return self.comment(credential, work_id, text, reply_to_id=target_id)
        return engagement_action(self.reader, credential, action, target_id, text=text)

    def delete_comment(self, credential: Credential, comment_id: str) -> JsonDocument:
        self._scope(credential)
        if not self.definition.engagement: raise ChannelError(self.definition.platform, 'engagement_unsupported', status=405)
        if not comment_id or len(comment_id) > 512: raise ChannelError(self.definition.platform, 'comment_id_invalid')
        return delete_comment(self.reader, credential, comment_id)

    def create_board(self, credential: Credential, name: str, *, description: str = '', privacy: str = 'PUBLIC') -> JsonDocument:
        self._scope(credential)
        if self.definition.platform != 'pinterest': raise ChannelError(self.definition.platform, 'create_board_unsupported', status=405)
        if not name.strip() or len(name) > 180 or len(description) > 500 or privacy not in {'PUBLIC', 'SECRET', 'PROTECTED'}:
            raise ChannelError('pinterest', 'board_options_invalid')
        return self.reader.call('POST', '/boards', credential, body={'name': name, 'description': description, 'privacy': privacy}).data

    def locations(self, credential: Credential, account_name: str) -> JsonDocument:
        self._scope(credential)
        if self.definition.platform != 'google-business': raise ChannelError(self.definition.platform, 'locations_unsupported', status=405)
        from easel.channels.business import business_accounts, business_locations
        if account_name not in {str(account.get('name')) for account in business_accounts(self.reader, credential)}:
            raise ChannelError('google-business', 'account_not_selectable', status=403)
        return {'locations': business_locations(self.reader, credential, account_name)}
