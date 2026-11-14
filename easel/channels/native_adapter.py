"""Adapter dispatch for registered native protocols and explicit user handoff."""
from pathlib import Path

from easel.channels.bilibili_open import BilibiliOpen
from easel.channels.douyin_open import DouyinOpen
from easel.channels.douyin_miniapp import DouyinMiniApp
from easel.channels.kwai_open import KwaiOpen
from easel.channels.models import AccountProfile, ChannelError, Credential, PublishContent, PublishReceipt
from easel.channels.reads import _document, _rows
from easel.channels.wechat_open import WechatOpen
from easel.native_topics import strip_topics, topic_insertions


class NativeAdapter:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.platform = adapter.definition.platform
        kwargs = {'transport': adapter.reader.http.transport}
        match self.platform:
            case 'bilibili-open': self.client = BilibiliOpen(adapter.settings, **kwargs)
            case 'kwai-open': self.client = KwaiOpen(adapter.settings, **kwargs)
            case 'douyin-open': self.client = None
            case 'wechat-official-open' | 'wechat-channels-open': self.client = WechatOpen(self.platform, adapter.settings, store=adapter.store, **kwargs)
            case 'rednote-link': self.client = None
            case _: raise ChannelError(self.platform, 'native_platform_invalid')

    def profile(self, credential: Credential) -> AccountProfile:
        if self.platform == 'douyin-open':
            if credential.profile and credential.profile.extra.get('auth_mode') == 'miniapp_qr':
                mini = DouyinMiniApp(self.adapter.settings, transport=self.adapter.reader.http.transport, store=self.adapter.store)
                home = _document(credential.auxiliary_tokens.get('homepage'))
                try:
                    data = mini.get_fans_data(str(home.get('access_token') or ''), credential.account_uid)
                    count = self.adapter.reader._count(data.get('fans_count'))
                except ChannelError:
                    count = None
                return credential.profile.model_copy(update={'fans_count': count})
            value = DouyinOpen(self.adapter.settings, transport=self.adapter.reader.http.transport, store=self.adapter.store).request('GET', '/oauth/userinfo/', headers={'access-token': credential.access_token.get_secret_value()}, params={'open_id': credential.account_uid})
            uid = str(value.get('open_id') or '')
            if not uid or credential.account_uid not in {'pending', uid}: raise ChannelError(self.platform, 'account_owner_mismatch', status=403)
            return AccountProfile(account_uid=uid, display_name=str(value.get('nickname') or ''), avatar_url=str(value.get('avatar') or ''))
        if self.client is None: raise ChannelError(self.platform, 'profile_unsupported', status=405)
        return self.client.profile(credential)

    def works(self, credential: Credential, cursor: str = '', limit: int = 25, direction: str = 'next'):
        if len(cursor) > 1024 or type(limit) is not int or limit < 1: raise ChannelError(self.platform, 'pagination_invalid')
        if direction != 'next': raise ChannelError(self.platform, 'previous_pagination_unsupported', status=405)
        if self.platform not in {'bilibili-open', 'kwai-open'}: raise ChannelError(self.platform, 'work_list_unsupported', status=405)
        return self.client.works(credential, cursor, limit)

    def work(self, credential: Credential, work_id: str):
        if self.platform == 'douyin-open' and credential.profile and credential.profile.extra.get('auth_mode') == 'miniapp_qr':
            value = DouyinMiniApp(self.adapter.settings, transport=self.adapter.reader.http.transport, store=self.adapter.store).query_video_data(
                credential.access_token.get_secret_value(), credential.account_uid, item_ids=[work_id])
            rows = _rows(value.get('videos') or value.get('list') or value.get('video_list'))
            row = next((row for row in rows if str(row.get('item_id') or row.get('video_id')) == work_id), None)
            if row is None: raise ChannelError(self.platform, 'work_owner_unverified', status=403)
            return {**row, 'id': work_id, 'author_id': credential.account_uid}
        if self.platform not in {'bilibili-open', 'kwai-open', 'wechat-channels-open'}: raise ChannelError(self.platform, 'work_detail_unsupported', status=405)
        return self.client.work(credential, work_id)

    def analytics(self, credential: Credential, work_id: str = ''):
        if self.platform not in {'bilibili-open', 'kwai-open'}: raise ChannelError(self.platform, 'analytics_unsupported', status=405)
        if work_id: self.work(credential, work_id)
        return self.client.analytics(credential, work_id)

    def options(self, credential: Credential):
        return self.client.options(credential) if self.platform == 'bilibili-open' else {'modes': list(self.adapter.definition.publish_modes)}

    def resolve_link(self, credential: Credential, link: str):
        if self.platform == 'rednote-link':
            from easel.channels.links import rednote_link_info
            return rednote_link_info(link)
        if self.platform == 'wechat-channels-open': return self.client.resolve_link(credential, link)
        from easel.channels.links import expand_short_link, link_identity
        identity, canonical = link_identity(self.platform, expand_short_link(self.platform, link))
        if self.platform in {'douyin-open', 'rednote-link'}:
            return {'platform_work_id': identity, 'permalink': canonical, 'ownership_verified': False}
        return {'platform_work_id': identity, 'permalink': canonical, 'work': self.work(credential, identity)}

    def publish(self, credential: Credential, content: PublishContent, *, media_paths=(), cover_path: Path | None = None):
        if self.platform in {'bilibili-open', 'kwai-open'}: return self.client.publish(credential, content, cover_path)
        if self.platform == 'wechat-official-open': return self.client.publish(credential, content, media_paths=media_paths, cover_path=cover_path)
        if self.platform == 'douyin-open':
            options = {'short_title': content.title, 'download_type': 1, 'private_status': 0,
                **dict(content.options), 'title_hashtag_list': topic_insertions(content.body)}
            value = DouyinOpen(self.adapter.settings, transport=self.adapter.reader.http.transport, store=self.adapter.store).generate_handoff(title=strip_topics(content.body) if content.body else content.title,
                video_url=next((media.url for media in content.media if media.kind == 'video'), ''),
                image_urls=[media.url for media in content.media if media.kind == 'image'], options=options)
            return PublishReceipt(state='requires_user_action', external_request_id=str(value['share_id']), details={**value,
                'action_url': value['schema'], 'account_uid': credential.account_uid})
        link = str(content.options.get('workLink') or '')
        identity, canonical = str(content.options.get('workId') or ''), ''
        if link:
            if self.platform == 'wechat-channels-open':
                value = self.resolve_link(credential, link)
                identity, canonical = str(value['platform_work_id']), str(value['permalink'])
            else:
                from easel.channels.links import link_identity
                identity, canonical = link_identity(self.platform, link)
        if not link and not identity: raise ChannelError(self.platform, 'work_link_required')
        return PublishReceipt(state='requires_user_action', details={'account_uid': credential.account_uid,
            'candidate_work_id': identity, 'candidate_permalink': canonical, 'ownership_verified': False,
            'link_status': content.options.get('linkStatus', 'pending'), 'link_meta': content.options.get('linkMeta', {})})

    def finalize(self, credential: Credential, receipt: PublishReceipt):
        if self.platform in {'bilibili-open', 'kwai-open', 'wechat-official-open'}: return self.client.finalize(credential, receipt)
        if self.platform == 'douyin-open':
            value = DouyinOpen(self.adapter.settings, transport=self.adapter.reader.http.transport, store=self.adapter.store).get_share_publish_result(receipt.external_request_id)
            identity = str(value.get('video_id') or '')
            if not identity or value.get('open_id') != credential.account_uid: return receipt
            return receipt.model_copy(update={'state': 'published', 'platform_work_id': identity,
                'permalink': 'https://www.douyin.com/video/' + identity, 'details': {**receipt.details,
                    'provider_verified': True, 'item_id': value.get('item_id')}})
        if self.platform == 'wechat-channels-open' and receipt.details.get('candidate_work_id'):
            identity = str(receipt.details['candidate_work_id'])
            self.work(credential, identity)
            return receipt.model_copy(update={'state': 'published', 'platform_work_id': identity,
                'permalink': str(receipt.details.get('candidate_permalink') or ''), 'details': {**receipt.details, 'provider_verified': True}})
        return receipt

    def initialize_upload(self, credential: Credential, path: Path):
        if self.platform not in {'bilibili-open', 'kwai-open'}: raise ChannelError(self.platform, 'upload_unsupported', status=405)
        return self.client.initialize_upload(credential, path)

    def upload(self, credential: Credential, receipt: PublishReceipt, path: Path):
        if self.platform not in {'bilibili-open', 'kwai-open'}: raise ChannelError(self.platform, 'upload_unsupported', status=405)
        return self.client.upload(credential, receipt, path)
