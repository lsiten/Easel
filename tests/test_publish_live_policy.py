"""A policy change while awaiting confirmation prevents the final click."""
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace
from contextlib import nullcontext
import importlib

import pytest


def test_douyin_rechecks_live_policy_after_human_wait(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / 'skills/shared/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    publisher = importlib.import_module('douyin_publish')
    policy = tmp_path / '.env'
    policy.write_text('EASEL_PUBLISH_MODE=automated\n')
    monkeypatch.setenv('EASEL_PUBLISH_POLICY_FILE', str(policy))
    monkeypatch.delenv('EASEL_PUBLISH_MODE', raising=False)
    button = Mock()
    monkeypatch.setattr(publisher, '_wait_publish_button_ready', lambda page: button)
    def pause(length):
        policy.write_text('EASEL_PUBLISH_MODE=manual\n')
    monkeypatch.setattr(publisher.human_pace, 'pause_before_commit', pause)
    with pytest.raises(RuntimeError, match='暂停'):
        publisher._click_publish(Mock(), 50)
    button.click.assert_not_called()


def test_platform_kill_switch_is_reloaded_without_restart(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / 'skills/shared/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    guard = importlib.import_module('publish_guard')
    policy = tmp_path / '.env'
    policy.write_text('EASEL_PUBLISH_MODE=automated\n')
    monkeypatch.setenv('EASEL_PUBLISH_POLICY_FILE', str(policy))
    monkeypatch.delenv('EASEL_PUBLISH_MODE', raising=False)
    monkeypatch.delenv('EASEL_PUBLISH_DISABLED_PLATFORMS', raising=False)
    guard.require_publish_allowed('twitter')
    policy.write_text('EASEL_PUBLISH_DISABLED_PLATFORMS=twitter,douyin\n')
    with pytest.raises(RuntimeError):
        guard.require_publish_allowed('twitter')
    guard.require_publish_allowed('instagram')


@pytest.fixture
def publishing_guard(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'skills/shared/scripts'))
    policy = tmp_path / '.env'
    policy.write_text('EASEL_PUBLISH_MODE=automated\n')
    monkeypatch.setenv('EASEL_PUBLISH_POLICY_FILE', str(policy))
    monkeypatch.setenv('EASEL_PUBLISH_ACCOUNT_UID', 'reviewed-actor')
    monkeypatch.delenv('EASEL_PUBLISH_MODE', raising=False)
    monkeypatch.delenv('EASEL_PUBLISH_DISABLED_PLATFORMS', raising=False)
    return policy


@pytest.mark.parametrize('action', ['click', 'js_click'])
@pytest.mark.parametrize('change', ['actor', 'policy', 'same'])
def test_generic_final_step_checks_actor_and_policy_after_human_pause(publishing_guard, monkeypatch, tmp_path, change, action):
    import playwright.sync_api
    publisher = importlib.import_module('web_publisher')
    monkeypatch.setenv('EASEL_PUBLISH_PLATFORM', 'zhihu')
    actor = {'id': 'reviewed-actor'}
    page, button = Mock(), Mock()
    page.url = 'https://zhuanlan.zhihu.com/write'
    page.context.request.get.side_effect = lambda *args, **kwargs: SimpleNamespace(status=200, json=lambda: dict(actor))
    browser = SimpleNamespace(pages=[page], close=Mock())
    monkeypatch.setattr(playwright.sync_api, 'sync_playwright', lambda: nullcontext(SimpleNamespace()))
    monkeypatch.setattr(publisher, 'launch_persistent_context', lambda *args: browser)
    monkeypatch.setattr(publisher, '_settle_login', lambda *args: None)
    monkeypatch.setattr(publisher, '_is_logged_in', lambda *args: True)
    monkeypatch.setattr(publisher, '_first_visible_el', lambda *args: button)
    monkeypatch.setitem(publisher.PLATFORMS, 'zhihu', {'name': '知乎', 'profile': 'ZhihuProfile', 'publish_url': page.url,
        'steps': [{'action': action, 'commit': True, 'selector': 'button'}]})
    def pause(length):
        if change == 'actor': actor['id'] = 'another-actor'
        if change == 'policy': publishing_guard.write_text('EASEL_PUBLISH_MODE=manual\n')
    monkeypatch.setattr(publisher.human_pace, 'pause_before_commit', pause)
    args = SimpleNamespace(platform='zhihu', profile_base=str(tmp_path), media='', title='Reviewed', desc='Body', tags='', cover='', keep_open=False)
    if change == 'same':
        assert publisher._run_browser(args, headed=False, do_publish=True) == 0
        assert button.click.call_count == 1
    else:
        with pytest.raises(RuntimeError, match='暂停|账号'):
            publisher._run_browser(args, headed=False, do_publish=True)
        button.click.assert_not_called()


@pytest.mark.parametrize('change', ['actor', 'policy', 'same'])
def test_douyin_final_actor_is_read_after_scroll_wait(publishing_guard, monkeypatch, change):
    publisher = importlib.import_module('douyin_publish')
    monkeypatch.setenv('EASEL_PUBLISH_PLATFORM', 'douyin')
    actor = {'platform_account_id': 'reviewed-actor'}
    monkeypatch.setattr(publisher.platform_readback, 'read_douyin_account', lambda page: dict(actor))
    page, button = Mock(), Mock()
    page.url = 'https://creator.douyin.com/creator-micro/content/publish'
    monkeypatch.setattr(publisher, '_wait_publish_button_ready', lambda page: button)
    monkeypatch.setattr(publisher.human_pace, 'pause_before_commit', lambda length: None)
    def wait(ms):
        if change == 'actor': actor['platform_account_id'] = 'switched'
        if change == 'policy': publishing_guard.write_text('EASEL_PUBLISH_MODE=manual\n')
    page.wait_for_timeout.side_effect = wait
    if change == 'same':
        publisher._click_publish(page, 50)
        button.click.assert_called_once()
    else:
        with pytest.raises(RuntimeError, match='暂停|账号'):
            publisher._click_publish(page, 50)
        button.click.assert_not_called()


@pytest.mark.parametrize('submit', ['click', 'enter'])
@pytest.mark.parametrize('change', ['actor', 'policy', 'same'])
def test_douyin_sms_checks_actor_after_typing_and_before_submit(publishing_guard, monkeypatch, change, submit):
    publisher = importlib.import_module('douyin_publish')
    monkeypatch.setenv('EASEL_PUBLISH_PLATFORM', 'douyin')
    actor = {'platform_account_id': 'reviewed-actor'}
    monkeypatch.setattr(publisher.platform_readback, 'read_douyin_account', lambda page: dict(actor))
    page, field, button = Mock(), Mock(), Mock()
    page.url = 'https://creator.douyin.com/creator-micro/content/publish'
    page.query_selector_all.return_value = [button] if submit == 'click' else []
    button.is_visible.return_value = True
    button.query_selector_all.return_value = []
    monkeypatch.setattr(publisher, '_find_verify_input', lambda page: field)
    field.input_value.return_value = '1234'
    def wait(ms):
        if ms == 700 and change == 'actor': actor['platform_account_id'] = 'switched'
        if ms == 700 and change == 'policy': publishing_guard.write_text('EASEL_PUBLISH_MODE=manual\n')
    page.wait_for_timeout.side_effect = wait
    if change == 'same':
        publisher._sms_fill_and_submit(page, '1234')
        assert button.click.call_count + sum(call.args == ('Enter',) for call in page.keyboard.press.call_args_list) == 1
    else:
        with pytest.raises(RuntimeError, match='账号|暂停'):
            publisher._sms_fill_and_submit(page, '1234')
        button.click.assert_not_called()
        page.keyboard.press.assert_any_call('Control+a')
        assert all(call.args != ('Enter',) for call in page.keyboard.press.call_args_list)


@pytest.mark.parametrize('actor', ['reviewed-actor', 'switched', ''])
def test_xhs_confirmation_uses_current_creator_actor_and_cannot_swallow_guard(publishing_guard, monkeypatch, actor):
    publisher = importlib.import_module('xhs_publish')
    monkeypatch.setenv('EASEL_PUBLISH_PLATFORM', 'xiaohongshu')
    page, button = Mock(), Mock()
    page.url = 'https://creator.xiaohongshu.com/publish/publish'
    page.context.request.get.return_value = SimpleNamespace(status=200,
        json=lambda: {'success': True, 'data': {'userId': actor}})
    page.query_selector_all.return_value = [button]
    button.is_visible.return_value = True
    button.inner_text.return_value = '确认发布'
    if actor == 'reviewed-actor':
        publisher._confirm_publish_dialog(page)
        button.click.assert_called_once()
    else:
        with pytest.raises(RuntimeError, match='账号'):
            publisher._confirm_publish_dialog(page)
        button.click.assert_not_called()


@pytest.mark.parametrize('platform', ['weixin-channels', 'bilibili', 'kuaishou'])
@pytest.mark.parametrize('actor', ['reviewed-actor', 'switched', ''])
def test_same_session_current_actor_contracts_fail_closed(publishing_guard, monkeypatch, platform, actor):
    guard = importlib.import_module('publish_guard')
    monkeypatch.setenv('EASEL_PUBLISH_PLATFORM', platform)
    page = Mock()
    page.url = {'weixin-channels': 'https://channels.weixin.qq.com/platform/post/create',
        'bilibili': 'https://member.bilibili.com/platform/upload/video/frame', 'kuaishou': 'https://cp.kuaishou.com/article/publish/video'}[platform]
    page.evaluate.return_value = {'errCode': 0, 'data': {'finderUser': {'finderUsername': actor, 'nickname': '本人'}}}
    page.context.request.get.return_value = SimpleNamespace(status=200, json=lambda: {'code': 0, 'data': {'isLogin': True, 'mid': actor}})
    page.context.request.post.return_value = SimpleNamespace(status=200, json=lambda: {'result': 1, 'data': {'userId': actor, 'nickname': '本人'}})
    if actor == 'reviewed-actor': guard.require_publish_commit(platform, page)
    else:
        with pytest.raises(RuntimeError, match='账号'): guard.require_publish_commit(platform, page)
    page.goto.assert_not_called()


def test_missing_expected_actor_preserves_standalone_cli_and_bound_lookup_errors_fail_closed(publishing_guard, monkeypatch):
    guard = importlib.import_module('publish_guard')
    lookup = Mock(side_effect=OSError('identity unavailable'))
    monkeypatch.setattr(guard, 'authenticated_uid', lookup)
    monkeypatch.setenv('EASEL_PUBLISH_PLATFORM', 'zhihu')
    monkeypatch.delenv('EASEL_PUBLISH_ACCOUNT_UID')
    guard.require_publish_commit('zhihu', Mock())
    lookup.assert_not_called()
    monkeypatch.setenv('EASEL_PUBLISH_ACCOUNT_UID', 'reviewed-actor')
    with pytest.raises(RuntimeError, match='无法核验'):
        guard.require_publish_commit('zhihu', Mock())


@pytest.mark.parametrize('change', ['actor', 'policy', 'same'])
def test_channels_composer_rechecks_before_trusted_enter_and_never_retries_guard_failure(publishing_guard, monkeypatch, change):
    publisher = importlib.import_module('web_publisher')
    monkeypatch.setenv('EASEL_PUBLISH_PLATFORM', 'weixin-channels')
    page, frame = Mock(), Mock()
    page.url = 'https://channels.weixin.qq.com/platform/post/create'
    frame.url = 'https://channels.weixin.qq.com/micro/content/post/create'
    page.frames = [frame]
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.nth.return_value.is_visible.return_value = True
    actor = {'uid': 'reviewed-actor'}
    page.evaluate.side_effect = lambda script: {'errCode': 0, 'data': {'finderUser': {'finderUsername': actor['uid'], 'nickname': '本人'}}}
    frame.evaluate.side_effect = lambda script, *args: True if '!!document' in script else 'focused'
    monkeypatch.setattr(publisher, '_settle_login', lambda *args: None)
    monkeypatch.setattr(publisher, '_is_logged_in', lambda *args: True)
    def wait(ms):
        if ms == 1200:
            if change == 'actor': actor['uid'] = 'switched'
            if change == 'policy': publishing_guard.write_text('EASEL_PUBLISH_MODE=manual\n')
    page.wait_for_timeout.side_effect = wait
    page.keyboard.press.side_effect = lambda key: setattr(page, 'url', 'https://channels.weixin.qq.com/platform/post/list')
    if change == 'same':
        publisher._publish_weixin_channels(page, {'media': 'approved.mp4', 'title': 'Reviewed'})
        page.keyboard.press.assert_called_once_with('Enter')
    else:
        with pytest.raises(RuntimeError, match='账号|暂停'):
            publisher._publish_weixin_channels(page, {'media': 'approved.mp4', 'title': 'Reviewed'})
        page.keyboard.press.assert_not_called()


@pytest.mark.parametrize('kind', ['new', 'old'])
@pytest.mark.parametrize('change', ['actor', 'same'])
def test_xhs_final_publish_uses_current_account_after_last_ui_wait(publishing_guard, monkeypatch, change, kind):
    publisher = importlib.import_module('xhs_publish')
    monkeypatch.setenv('EASEL_PUBLISH_PLATFORM', 'xiaohongshu')
    page, button = Mock(), Mock()
    page.url = 'https://creator.xiaohongshu.com/publish/publish'
    actor = {'uid': 'reviewed-actor'}
    page.context.request.get.side_effect = lambda *args, **kwargs: SimpleNamespace(status=200,
        json=lambda: {'success': True, 'data': {'userId': actor['uid']}})
    monkeypatch.setattr(publisher, '_content_element', lambda page: Mock())
    for name in ('_human_type', '_input_tags', '_check_overflow', '_confirm_publish_dialog', '_wait_publish_success'):
        monkeypatch.setattr(publisher, name, lambda *args: None)
    monkeypatch.setattr(publisher, '_wait_publish_clickable', lambda *args: (kind, button))
    button.bounding_box.return_value = {'x': 1, 'y': 2, 'width': 100, 'height': 20}
    def wait(ms):
        if ms == 300 and change == 'actor': actor['uid'] = 'switched'
    page.wait_for_timeout.side_effect = wait
    if change == 'same':
        publisher._fill_and_submit(page, 'Reviewed', 'Body', [])
        assert button.click.call_count + page.mouse.click.call_count == 1
    else:
        with pytest.raises(RuntimeError, match='账号'):
            publisher._fill_and_submit(page, 'Reviewed', 'Body', [])
        button.click.assert_not_called()
        page.mouse.click.assert_not_called()
