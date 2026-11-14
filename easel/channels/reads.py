"""Authenticated account, work, and insight reads for official platform APIs."""
from __future__ import annotations

from urllib.parse import quote

from easel.channels.http import ProviderHttp
from easel.channels.models import AccountProfile, ChannelError, Credential, JsonDocument, PlatformDefinition
from easel.channels.pagination import validate_pagination


def _document(value) -> JsonDocument:
    return value if isinstance(value, dict) else {}


def _rows(value) -> list[JsonDocument]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


class ChannelReads:
    def __init__(self, spec: PlatformDefinition, http: ProviderHttp) -> None:
        self.spec, self.http = spec, http

    def call(self, method: str, path: str, credential: Credential, *, params=None, body=None, headers=None):
        headers = {**({'LinkedIn-Version': '202605', 'X-Restli-Protocol-Version': '2.0.0'} if self.spec.platform == 'linkedin' else {}), **(headers or {})}
        return self.http.send(method, self.spec.api_base + path, token=credential.access_token.get_secret_value(),
                              params=params, body=body, headers=headers)

    def profile(self, credential: Credential) -> AccountProfile:
        match self.spec.platform:
            case 'youtube':
                data = self.call('GET', '/channels', credential, params={'part': 'snippet,statistics', 'mine': 'true'}).data
                rows = _rows(data.get('items'))
                if not rows:
                    raise ChannelError('youtube', 'channel_not_found', status=404)
                row = rows[0]
                if credential.account_uid != 'pending':
                    row = next((item for item in rows if str(item.get('id')) == credential.account_uid), None)
                    if row is None: raise ChannelError('youtube', 'authenticated_channel_changed', status=403)
                snippet, stats = _document(row.get('snippet')), _document(row.get('statistics'))
                thumbnail = _document(_document(snippet.get('thumbnails')).get('default'))
                return AccountProfile(account_uid=str(row['id']), display_name=str(snippet.get('title') or ''),
                    username=str(snippet.get('customUrl') or ''), avatar_url=str(thumbnail.get('url') or ''), fans_count=self._count(stats.get('subscriberCount')))
            case 'tiktok':
                data = self.call('GET', '/user/info/', credential, params={'fields': 'open_id,username,display_name,avatar_url,follower_count,following_count,likes_count,video_count'}).data
                row = _document(_document(data.get('data')).get('user'))
                return self._profile(row, 'open_id', 'display_name', 'avatar_url')
            case 'instagram':
                row = self.call('GET', '/me', credential, params={'fields': 'id,user_id,username,name,profile_picture_url'}).data
                return self._profile(row, 'user_id' if row.get('user_id') else 'id', 'username', 'profile_picture_url')
            case 'facebook':
                row = self.call('GET', '/me', credential, params={'fields': 'id,name,picture'}).data
                return self._profile(row, 'id', 'name')
            case 'threads':
                row = self.call('GET', '/me', credential, params={'fields': 'id,username,name,threads_profile_picture_url'}).data
                return self._profile(row, 'id', 'username', 'threads_profile_picture_url')
            case 'twitter':
                data = self.call('GET', '/users/me', credential, params={'user.fields': 'name,username,profile_image_url,public_metrics'}).data
                return self._profile(_document(data.get('data')), 'id', 'name', 'profile_image_url')
            case 'linkedin':
                row = self.http.send('GET', 'https://api.linkedin.com/v2/userinfo', token=credential.access_token.get_secret_value()).data
                return self._profile(row, 'sub', 'name', 'picture')
            case 'pinterest':
                row = self.call('GET', '/user_account', credential).data
                return self._profile(row, 'id', 'business_name' if row.get('business_name') else 'username', 'profile_image')
            case 'google-business':
                if credential.parent_uid and '/locations/' in credential.account_uid:
                    from easel.channels.business import business_profiles
                    profile = next((item for item in business_profiles(self, credential) if item.account_uid == credential.account_uid), None)
                    if profile is None: raise ChannelError('google-business', 'location_access_revoked', status=403)
                    return profile
                row = self.http.send('GET', 'https://www.googleapis.com/oauth2/v3/userinfo', token=credential.access_token.get_secret_value()).data
                return self._profile(row, 'sub', 'name', 'picture')

    def _profile(self, row: JsonDocument, id_key: str, name_key: str, avatar_key: str = '') -> AccountProfile:
        uid = row.get(id_key)
        if not isinstance(uid, (str, int)) or not str(uid):
            raise ChannelError(self.spec.platform, 'profile_identity_missing', status=502)
        metrics = _document(row.get('public_metrics'))
        avatar = row.get(avatar_key) or _document(_document(row.get('picture')).get('data')).get('url') or ''
        return AccountProfile(account_uid=str(uid), display_name=str(row.get(name_key) or ''), avatar_url=str(avatar),
            username=str(row.get('username') or ''), email=str(row.get('email') or ''),
            fans_count=self._count(row.get('follower_count') if 'follower_count' in row else row.get('followers_count', row.get('fan_count', metrics.get('followers_count')))),
            following_count=self._count(row.get('following_count', row.get('follows_count', metrics.get('following_count')))))

    @staticmethod
    def _count(value: object) -> int | None:
        if type(value) is int and value >= 0: return value
        if isinstance(value, str) and value.isdecimal(): return int(value)
        return None

    def selectable_accounts(self, credential: Credential) -> tuple[AccountProfile, ...]:
        match self.spec.platform:
            case 'facebook':
                return tuple(self._profile(row, 'id', 'name').model_copy(update={'parent_uid': credential.account_uid}) for row in self.facebook_pages(credential))
            case 'google-business':
                from easel.channels.business import business_profiles
                return business_profiles(self, credential)
            case 'youtube':
                data = self.call('GET', '/channels', credential, params={'part': 'snippet', 'mine': 'true', 'maxResults': '50'}).data
                return tuple(AccountProfile(account_uid=str(row['id']), display_name=str(_document(row.get('snippet')).get('title') or '')) for row in _rows(data.get('items')))
            case _:
                return (self.profile(credential),)

    def facebook_pages(self, credential: Credential) -> list[JsonDocument]:
        pages, cursor, seen = [], '', set()
        while True:
            result = self.call('GET', '/me/accounts', credential, params={'fields': 'id,name,access_token,tasks,picture,followers_count,fan_count,category,category_list', 'limit': '100',
                **({'after': cursor} if cursor else {})}).data
            pages.extend(_rows(result.get('data')))
            paging = _document(result.get('paging'))
            cursor = _document(paging.get('cursors')).get('after') if paging.get('next') else ''
            if not isinstance(cursor, str) or not cursor: return pages
            if cursor in seen: raise ChannelError('facebook', 'account_pagination_cycle', status=502)
            seen.add(cursor)

    def works(self, credential: Credential, cursor: str = '', *, limit: int = 25, direction: str = 'next') -> JsonDocument:
        if not self.spec.works:
            raise ChannelError(self.spec.platform, 'works_unsupported', status=405)
        uid = quote(credential.account_uid, safe='')
        limit = validate_pagination(self.spec.platform, cursor, limit, direction)
        graph_cursor = {('before' if direction == 'previous' else 'after'): cursor} if cursor else {}
        match self.spec.platform:
            case 'youtube':
                params = {'part': 'snippet', 'forMine': 'true', 'type': 'video', 'maxResults': str(limit)}
                if cursor: params['pageToken'] = cursor
                return self.call('GET', '/search', credential, params=params).data
            case 'tiktok':
                body: JsonDocument = {'max_count': limit}
                if cursor:
                    if not cursor.isdecimal(): raise ChannelError('tiktok', 'cursor_invalid')
                    body['cursor'] = int(cursor)
                return self.call('POST', '/video/list/', credential, body=body,
                                 params={'fields': 'id,title,share_url,view_count,like_count,comment_count,share_count'}).data
            case 'instagram':
                return self.call('GET', f'/{uid}/media', credential,
                                 params={'fields': 'id,caption,media_type,permalink,timestamp', 'limit': str(limit), **graph_cursor}).data
            case 'facebook':
                return self.call('GET', f'/{uid}/published_posts', credential,
                                 params={'fields': 'id,message,permalink_url,created_time', 'limit': str(limit), **graph_cursor}).data
            case 'threads':
                return self.call('GET', f'/{uid}/threads', credential,
                                 params={'fields': 'id,text,permalink,timestamp,media_type', 'limit': str(limit), **graph_cursor}).data
            case 'twitter':
                return self.call('GET', f'/users/{uid}/tweets', credential,
                                 params={'tweet.fields': 'created_at,public_metrics,author_id', 'max_results': str(max(5, limit)), **({'pagination_token': cursor} if cursor else {})}).data
            case 'pinterest':
                return self.call('GET', '/pins', credential, params={'page_size': str(limit), **({'bookmark': cursor} if cursor else {})}).data
            case _:
                raise ChannelError(self.spec.platform, 'works_unsupported', status=405)

    def work(self, credential: Credential, work_id: str) -> JsonDocument:
        if not self.spec.works:
            raise ChannelError(self.spec.platform, 'works_unsupported', status=405)
        work = quote(work_id, safe='')
        match self.spec.platform:
            case 'youtube':
                result = self.call('GET', '/videos', credential, params={'part': 'snippet,status,statistics', 'id': work_id}).data
                rows = _rows(result.get('items'))
                if len(rows) != 1 or _document(rows[0].get('snippet')).get('channelId') != credential.account_uid:
                    raise ChannelError('youtube', 'work_owner_mismatch', status=403)
                return rows[0]
            case 'tiktok':
                result = self.call('POST', '/video/query/', credential, body={'filters': {'video_ids': [work_id]}},
                                 params={'fields': 'id,title,create_time,share_url,view_count,like_count,comment_count,share_count'}).data
                videos = _rows(_document(result.get('data')).get('videos'))
                if len(videos) != 1 or str(videos[0].get('id')) != work_id:
                    raise ChannelError('tiktok', 'work_owner_unverified', status=403)
                return result
            case 'instagram':
                return self.call('GET', f'/{work}', credential, params={'fields': 'id,username,caption,permalink,media_type,timestamp'}).data
            case 'facebook':
                return self.call('GET', f'/{work}', credential, params={'fields': 'id,from,message,permalink_url,created_time'}).data
            case 'threads':
                return self.call('GET', f'/{work}', credential, params={'fields': 'id,username,text,permalink,timestamp'}).data
            case 'twitter':
                result = self.call('GET', f'/tweets/{work}', credential, params={'tweet.fields': 'author_id,public_metrics,created_at'}).data
                if _document(result.get('data')).get('author_id') != credential.account_uid:
                    raise ChannelError('twitter', 'work_owner_mismatch', status=403)
                return result
            case 'pinterest':
                return self.call('GET', f'/pins/{work}', credential).data
            case _:
                raise ChannelError(self.spec.platform, 'works_unsupported', status=405)

    def analytics(self, credential: Credential, work_id: str = '', *, since: str = '', until: str = '') -> JsonDocument:
        if not self.spec.analytics:
            raise ChannelError(self.spec.platform, 'analytics_unsupported', status=405)
        uid = quote(credential.account_uid, safe='')
        work = quote(work_id, safe='')
        dates = {**({'since': since} if since else {}), **({'until': until} if until else {})}
        match self.spec.platform:
            case 'youtube':
                return self.work(credential, work_id) if work_id else self.call('GET', '/channels', credential, params={'mine': 'true', 'part': 'statistics'}).data
            case 'tiktok':
                return self.work(credential, work_id) if work_id else self.call('GET', '/user/info/', credential,
                    params={'fields': 'open_id,follower_count,following_count,likes_count,video_count'}).data
            case 'instagram':
                metrics = 'reach,likes,comments,shares,saved,total_interactions' if work_id else 'reach,profile_views,website_clicks'
                result = self.call('GET', f'/{work or uid}/insights', credential, params={'metric': metrics, **dates, **({} if work_id else {'period': 'day'})}).data
                if not work_id:
                    result['profile'] = self.call('GET', f'/{uid}', credential, params={'fields': 'id,username,name,profile_picture_url,followers_count,follows_count,media_count'}).data
                return result
            case 'facebook':
                if not work_id:
                    return self.call('GET', f'/{uid}', credential, params={'fields': 'id,name,fan_count,followers_count,picture,category,category_list'}).data
                return self.call('GET', f'/{work}/insights', credential,
                                 params={'metric': 'post_impressions,post_engaged_users,post_clicks,post_reactions_by_type_total', **dates}).data
            case 'threads':
                return self.call('GET', f'/{work or uid}/' + ('insights' if work_id else 'threads_insights'), credential,
                                 params={'metric': 'views,likes,replies,reposts,quotes,shares' if work_id else 'views,likes,replies,reposts,quotes,followers_count', **dates}).data
            case 'twitter':
                return self.work(credential, work_id) if work_id else self.call('GET', '/users/me', credential, params={'user.fields': 'public_metrics'}).data
            case 'pinterest':
                if not work_id:
                    raise ChannelError('pinterest', 'work_id_required')
                raise ChannelError('pinterest', 'analytics_date_range_required')
            case _:
                raise ChannelError(self.spec.platform, 'analytics_unsupported', status=405)
