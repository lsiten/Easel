import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/shared/scripts'))
import browser_identity
import bili_login
import platform_readback
import web_publisher


@pytest.mark.parametrize('status,payload,expected', [
    (200, {'id': 'current-user', 'name': '本人'}, 'current-user'),
    (401, {'id': 'public-author'}, ''),
    (200, {'error': {'message': 'Unauthorized'}, 'id': 'public-author'}, ''),
    (200, {'id': True}, ''),
    (200, {'name': '昵称'}, ''),
])
def test_zhihu_uid_is_from_authenticated_me_response(status, payload, expected):
    get = Mock(return_value=SimpleNamespace(status=status, json=lambda: payload))
    page = SimpleNamespace(url='https://zhuanlan.zhihu.com/write', context=SimpleNamespace(request=SimpleNamespace(get=get)))
    assert browser_identity.authenticated_uid('zhihu', page) == expected
    get.assert_called_once_with('https://www.zhihu.com/api/v4/me', timeout=5000)


@pytest.mark.parametrize('visible,host,expected', [
    (True, 'www.csdn.net', 'own-name'), (False, 'blog.csdn.net', ''), (True, 'foreign.example', ''),
])
def test_csdn_cookie_needs_current_authenticated_toolbar_and_platform_origin(visible, host, expected):
    cookies = Mock(return_value=[{'name': 'UserName', 'value': 'own-name'}, {'name': 'token', 'value': 'private'}])
    page = SimpleNamespace(url='https://' + host + '/', evaluate=lambda script: visible,
                           context=SimpleNamespace(cookies=cookies))
    assert browser_identity.authenticated_uid('csdn', page) == expected
    if not expected:
        cookies.assert_not_called()


@pytest.mark.parametrize('url,value,expected', [
    ('https://mp.weixin.qq.com/cgi-bin/home?token=session-only', 'gh_current', 'gh_current'),
    ('https://mp.weixin.qq.com/cgi-bin/login', 'gh_other', ''),
    ('https://foreign.example/?token=session-only', 'gh_other', ''),
])
def test_wechat_uid_requires_authenticated_platform_document(url, value, expected):
    page = SimpleNamespace(url=url, evaluate=Mock(return_value=value))
    assert browser_identity.authenticated_uid('wechat-oa', page) == expected
    assert 'session-only' not in expected


def test_kuaishou_identity_reuses_authenticated_account_readback(monkeypatch):
    page = SimpleNamespace(url='https://cp.kuaishou.com/')
    read = Mock(return_value={'platform_account_id': 'current-id', 'display_name': '本人'})
    monkeypatch.setattr(platform_readback, 'read_kuaishou_account', read)
    assert browser_identity.authenticated_uid('kuaishou', page) == 'current-id'
    read.assert_called_once_with(page)


def test_bilibili_whoami_reports_server_mid_over_cookie_metadata(monkeypatch, capsys):
    monkeypatch.setattr(bili_login, '_load_cookie', lambda path: ('authenticated-cookie', 999))
    api = Mock(return_value={'code': 0, 'data': {'isLogin': True, 'mid': 42, 'uname': '本人', 'face': 'https://face.example/1'}})
    monkeypatch.setattr(bili_login, '_api', api)
    assert bili_login.cmd_whoami(SimpleNamespace(cookie='fixture.json')) == 0
    identity = json.loads(capsys.readouterr().out)
    assert identity['accountUid'] == '42'
    assert identity['loggedIn'] is True


def test_csdn_configured_profile_cannot_override_authenticated_username(monkeypatch):
    import account_stats
    navigated = []
    page = SimpleNamespace(url='https://my.csdn.net/main/my-article',
        goto=lambda url, **kwargs: navigated.append(url), evaluate=lambda script: True,
        context=SimpleNamespace(cookies=lambda urls: [{'name': 'UserName', 'value': 'own-user'}]))
    monkeypatch.setattr(account_stats, '_login_wall', lambda page: False)
    monkeypatch.setattr(account_stats, '_looks_blocked', lambda page: False)
    monkeypatch.setattr(account_stats, '_platform_logged_in', lambda page, cfg: True)
    monkeypatch.setattr(account_stats, '_poll_stable', lambda *args: [])
    monkeypatch.setattr(account_stats, '_scrape_notes', lambda *args: [])
    initial = {'nickname': '', 'followers': None, 'likes': None, 'following': None, 'posts': None,
               'metrics': [], 'notes': [], 'logged_in': True}
    value = account_stats._scrape_csdn(page.context, page, initial, 'https://blog.csdn.net/other-author')
    assert value['account_uid'] == 'own-user'
    assert navigated == ['https://my.csdn.net/main/my-article', 'https://blog.csdn.net/own-user']
