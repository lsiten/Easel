"""Read stable current actors from authenticated browser evidence."""
from __future__ import annotations

import re
from typing import Protocol, TypedDict
from urllib.parse import parse_qs, urlsplit

from pydantic import JsonValue


class BrowserCookie(TypedDict):
    name: str
    value: str


class BrowserResponse(Protocol):
    status: int
    def json(self) -> JsonValue: ...


class BrowserRequest(Protocol):
    def get(self, url: str, *, timeout: int) -> BrowserResponse: ...
    def post(self, url: str, *, timeout: int) -> BrowserResponse: ...


class IdentityContext(Protocol):
    request: BrowserRequest
    def cookies(self, urls: list[str]) -> list[BrowserCookie]: ...


class IdentityPage(Protocol):
    url: str
    context: IdentityContext
    def evaluate(self, script: str) -> JsonValue: ...


def stable_uid(value: JsonValue) -> str:
    match value:  # noqa: MATCH_OK — validate an open JSON input and reject all other values
        case str():
            return value.strip() if re.fullmatch(r'[A-Za-z0-9_@.=-]{1,256}', value.strip()) else ''
        case int() if type(value) is int and value > 0:
            return str(value)
        case _:
            return ''


_CSDN_LOGGED_IN = """() => {
  const visible = e => !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  return visible(document.querySelector('a.hasAvatar')) &&
    !visible(document.querySelector('a.toolbar-btn-loginfun'));
}"""

_JUEJIN_ACTOR = """async () => {
  const visible = e => !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const avatar = document.querySelector('.main-header .avatar-wrapper, header .avatar-wrapper, .main-nav .avatar-wrapper');
  if (!visible(avatar)) return '';
  const selectors = 'header a[href], .main-header a[href], .main-nav a[href], .user-dropdown-menu a[href], .user-menu a[href]';
  const read = () => {
    for (const element of document.querySelectorAll(selectors)) {
      if (!visible(element) || (element.innerText || '').trim() !== '我的主页') continue;
      const target = new URL(element.href, location.href);
      const match = target.pathname.match(/^\\/user\\/(\\d+)\\/?$/);
      if (target.protocol === 'https:' && target.hostname === 'juejin.cn' && match) return match[1];
    }
    return '';
  };
  if (read()) return read();
  const focused = document.activeElement;
  avatar.click();
  try {
    await new Promise(resolve => setTimeout(resolve, 400));
    return read();
  } finally {
    avatar.click();
    await new Promise(resolve => setTimeout(resolve, 100));
    if (focused && focused.focus) focused.focus({preventScroll: true});
    if (read()) throw new Error('Account menu did not close');
  }
}"""

_WECHAT_ACTOR = """() => {
  const candidates = [window.wx && window.wx.cgiData, window.wx && window.wx.data, window.cgiData];
  for (const value of candidates) {
    if (!value) continue;
    const name = value.user_name || value.userName || value.biz;
    if (typeof name === 'string' && name) return name;
  }
  return '';
}"""

_CHANNELS_IDENTITY = """async () => {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 5000);
  try {
    const response = await fetch('/cgi-bin/mmfinderassistant-bin/auth/auth_data', {
      method: 'POST', credentials: 'include', signal: ctrl.signal,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({timestamp: String(Date.now()), _log_finder_uin: '',
        _log_finder_id: '', rawKeyBuff: null, pluginSessionId: null, scene: 7, reqScene: 7})
    });
    return response.ok ? await response.json() : null;
  } finally { clearTimeout(timer); }
}"""


def authenticated_uid(platform: str, page: IdentityPage, *, for_publish: bool = False) -> str:
    """Use same-session me responses or authenticated navigation, never a configured profile URL."""
    host = urlsplit(page.url).hostname or ''
    match platform:  # noqa: MATCH_OK — unsupported external platform strings yield no identity evidence
        case 'douyin':
            if for_publish and host != 'creator.douyin.com':
                return ''
            from platform_readback import read_douyin_account
            return stable_uid(read_douyin_account(page).get('platform_account_id'))
        case 'kuaishou':
            if for_publish:
                from platform_readback import _find_kuaishou_identity
                response = page.context.request.post('https://cp.kuaishou.com/rest/cp/works/v2/common/pc/current/user', timeout=5000)
                payload = response.json()
                if response.status != 200 or not isinstance(payload, dict) or payload.get('result') != 1:
                    return ''
                return stable_uid((_find_kuaishou_identity([{'data': payload}]) or {}).get('platform_account_id'))
            from platform_readback import read_kuaishou_account
            return stable_uid(read_kuaishou_account(page).get('platform_account_id'))
        case 'xiaohongshu':
            from xhs_publish import _creator_account
            return stable_uid((_creator_account(page.context) or {}).get('userId'))
        case 'weixin-channels':
            from web_publisher import _channels_account
            if host != 'channels.weixin.qq.com':
                return ''
            return stable_uid((_channels_account(page.evaluate(_CHANNELS_IDENTITY)) or {}).get('id'))
        case 'bilibili':
            response = page.context.request.get('https://api.bilibili.com/x/web-interface/nav', timeout=5000)
            payload = response.json()
            if response.status == 200 and isinstance(payload, dict) and payload.get('code') == 0:
                data = payload.get('data')
                return stable_uid(data.get('mid')) if isinstance(data, dict) and data.get('isLogin') is True else ''
            return ''
        case 'zhihu':
            response = page.context.request.get('https://www.zhihu.com/api/v4/me', timeout=5000)
            if response.status != 200:
                return ''
            payload = response.json()
            match payload:  # noqa: MATCH_OK — validate an untrusted provider JSON shape
                case dict() if not payload.get('error'):
                    return stable_uid(payload.get('id'))
                case _:
                    return ''
        case 'csdn':
            if not (host == 'csdn.net' or host.endswith('.csdn.net')) or page.evaluate(_CSDN_LOGGED_IN) is not True:
                return ''
            cookies = page.context.cookies(['https://www.csdn.net/', 'https://blog.csdn.net/'])
            return next((stable_uid(cookie['value']) for cookie in cookies if cookie['name'] == 'UserName'), '')
        case 'juejin':
            return stable_uid(page.evaluate(_JUEJIN_ACTOR)) if host == 'juejin.cn' else ''
        case 'wechat-oa':
            if host != 'mp.weixin.qq.com' or not parse_qs(urlsplit(page.url).query).get('token'):
                return ''
            return stable_uid(page.evaluate(_WECHAT_ACTOR))
        case _:
            return ''
