"""Resolve canonical work links while retaining authenticated ownership checks."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from easel.channels.models import ChannelError

_HOSTS = {
    'youtube': {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'},
    'tiktok': {'www.tiktok.com', 'tiktok.com'},
    'instagram': {'www.instagram.com', 'instagram.com'},
    'facebook': {'www.facebook.com', 'facebook.com', 'm.facebook.com'},
    'threads': {'www.threads.net', 'threads.net', 'www.threads.com', 'threads.com'},
    'twitter': {'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'},
    'pinterest': {'www.pinterest.com', 'pinterest.com'},
    'bilibili-open': {'www.bilibili.com', 'bilibili.com', 'm.bilibili.com'},
    'kwai-open': {'www.kuaishou.com', 'kuaishou.com', 'c.kuaishou.com'},
    'douyin-open': {'www.douyin.com', 'douyin.com', 'www.iesdouyin.com', 'iesdouyin.com'},
    'rednote-link': {'www.xiaohongshu.com', 'xiaohongshu.com', 'www.rednote.com', 'rednote.com'},
}
_SHORT_HOSTS = {'tiktok': {'vm.tiktok.com', 'vt.tiktok.com'}, 'pinterest': {'pin.it'},
                'twitter': {'t.co'}, 'facebook': {'fb.watch', 'fb.com'}, 'bilibili-open': {'b23.tv'},
                'kwai-open': {'v.kuaishou.com'}, 'douyin-open': {'v.douyin.com'}, 'rednote-link': {'xhslink.com', 'www.xhslink.com'}}


def expand_short_link(platform: str, link: str) -> str:
    from urllib import request, error
    from easel.remote_media import public_opener, RemoteMediaError
    parsed = urlsplit(link)
    if parsed.hostname not in _SHORT_HOSTS.get(platform, set()) and not (platform == 'kwai-open' and parsed.hostname in {'www.kuaishou.com', 'kuaishou.com'} and parsed.path.startswith('/f/')): return link
    allowed = _HOSTS[platform] | _SHORT_HOSTS[platform]
    def check(url: str) -> None:
        value = urlsplit(url)
        try:
            port = value.port
        except ValueError as error:
            raise ChannelError(platform, 'short_link_destination_invalid') from error
        if value.scheme != 'https' or port not in (None, 443) or value.hostname not in allowed or value.username or value.password:
            raise ChannelError(platform, 'short_link_destination_invalid')
    class ProviderRedirect(request.HTTPRedirectHandler):
        count = 0
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            self.count += 1
            check(newurl)
            if self.count > 3: raise ChannelError(platform, 'short_link_redirect_limit')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    check(link)
    try:
        with public_opener(ProviderRedirect()).open(request.Request(link, method='GET' if platform.endswith(('-open', '-link')) else 'HEAD'), timeout=15) as response:
            resolved = response.geturl()
            check(resolved)
            return resolved
    except (error.URLError, OSError, ValueError, RemoteMediaError) as failure:
        raise ChannelError(platform, 'short_link_resolution_failed', status=502) from failure


def link_identity(platform: str, link: str) -> tuple[str, str]:
    parsed = urlsplit(link)
    if platform not in _HOSTS: raise ChannelError(platform, 'work_links_unsupported', status=405)
    try:
        port = parsed.port
    except ValueError as error:
        raise ChannelError(platform, 'work_link_invalid') from error
    if parsed.scheme != 'https' or parsed.hostname not in _HOSTS[platform] or parsed.username or parsed.password or port not in (None, 443):
        raise ChannelError(platform, 'work_link_invalid')
    canonical = 'https://' + parsed.netloc + parsed.path.rstrip('/')
    if platform == 'rednote-link' and parsed.query: canonical += '?' + parsed.query
    if platform == 'youtube':
        identity = parse_qs(parsed.query).get('v', [''])[0]
        if not identity:
            identity = parsed.path.strip('/').split('/')[-1] if parsed.hostname == 'youtu.be' or parsed.path.startswith(('/shorts/', '/embed/')) else ''
        return identity, f'https://www.youtube.com/watch?v={identity}'
    expressions = {'tiktok': r'/(?:video|photo)/(\d+)', 'twitter': r'/status/(\d+)', 'pinterest': r'/pin/(\d+)',
                   'facebook': r'/(?:posts|videos|reel)/(\w+)', 'instagram': r'/(?:p|reel|tv)/([\w-]+)', 'threads': r'/post/([\w-]+)',
                   'bilibili-open': r'/video/(BV[\w]+|av\d+)', 'kwai-open': r'/(?:short-video|video|photo)/([\w-]+)',
                   'douyin-open': r'/(?:video|note|share/video)/(\d+)', 'rednote-link': r'/(?:explore|red_video|discovery/item|user/profile/[^/]+)/([\w-]+)'}
    match = re.search(expressions[platform], parsed.path, re.IGNORECASE if platform == 'bilibili-open' else 0)
    identity = match.group(1) if match else parse_qs(parsed.query).get('story_fbid', [''])[0] if platform == 'facebook' else ''
    if not identity and platform == 'douyin-open': identity = parse_qs(parsed.query).get('modal_id', [''])[0]
    if not identity: raise ChannelError(platform, 'work_link_invalid')
    if platform == 'bilibili-open': canonical = 'https://www.bilibili.com/video/' + identity
    if platform == 'kwai-open': canonical = 'https://www.kuaishou.com/short-video/' + identity
    if platform == 'douyin-open': canonical = 'https://www.douyin.com/' + ('note/' if parsed.path.startswith('/note/') else 'video/') + identity
    return identity, canonical


def rednote_link_info(link: str, data_id: str = '') -> dict:
    resolved = link
    try:
        resolved = expand_short_link('rednote-link', link)
        identity, canonical = link_identity('rednote-link', resolved)
    except ChannelError:
        parsed = urlsplit(link)
        if not data_id or len(data_id) > 512 or not re.fullmatch(r'[\w-]+', data_id) or parsed.scheme != 'https' or parsed.hostname not in _HOSTS['rednote-link'] | _SHORT_HOSTS['rednote-link'] or parsed.username or parsed.password:
            raise
        try:
            if parsed.port not in (None, 443): raise ChannelError('rednote-link', 'work_link_invalid')
        except ValueError as error:
            raise ChannelError('rednote-link', 'work_link_invalid') from error
        identity, canonical = '', link
    query = parse_qs(urlsplit(resolved).query)
    token = query.get('xsec_token') or query.get('xsecToken')
    return {'platform_work_id': identity or data_id, 'permalink': canonical, 'ownership_verified': False,
        'link_status': 'ready' if token else 'link_error' if identity else 'pending', 'original_work_link': link if link != resolved else '',
        'extra': {'dataId': identity or data_id, 'uniqueId': 'rednote_' + (identity or data_id), 'resolvedUrl': canonical}}


def native_link_info(platform: str, link: str, data_id: str = '') -> dict:
    if platform == 'rednote-link': return rednote_link_info(link, data_id)
    identity, canonical = link_identity(platform, expand_short_link(platform, link))
    return {'platform_work_id': identity, 'permalink': canonical, 'ownership_verified': False,
        'extra': {'dataId': identity, 'uniqueId': platform.removesuffix('-open') + '_' + identity, 'resolvedUrl': canonical}}
