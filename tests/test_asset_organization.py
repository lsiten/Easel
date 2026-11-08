"""AI plans are applied to the real library only within their persona/file scope."""
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from web.asset_organization import AssetOrganizer, organization_router


@pytest.fixture
def library(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'skills/shared/scripts/assets.py'
    spec = importlib.util.spec_from_file_location('asset_job_library', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'PROFILES_DIR', tmp_path / 'profiles')
    monkeypatch.setattr(module, 'REGISTRY', tmp_path / 'profiles/asset_scopes.json')
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'untitled.txt').write_text('南湖边的社区活动，适合作为城市纪录片素材。', encoding='utf-8')
    module.add('甲', [str(source)])
    return module, source


def client_for(tmp_path, library, runner):
    manager = AssetOrganizer(tmp_path / 'jobs', lambda: library, runner)
    app = FastAPI()
    app.include_router(organization_router(manager, lambda profile: profile in {'甲', '乙'}, lambda: None))
    return TestClient(app), manager


def plan_for(path, **changes):
    item = dict(file=str(path), category='城市活动', tags=['纪录片', '社区'], summary='南湖社区活动，可用于城市视频', kind='text')
    item.update(changes)
    return json.dumps({'items': [item]}, ensure_ascii=False)


@pytest.mark.parametrize('presentation', ['json', 'fenced-explanation', 'preamble-json'])
def test_ai_decision_changes_real_library_and_survives_refresh(tmp_path, library, presentation):
    engine, source = library
    result = plan_for(source / 'untitled.txt')
    if presentation == 'fenced-explanation':
        result = f'以下为分析结果：\n```json\n{result}\n```\n已读正文。'
    if presentation == 'preamble-json':
        result = f'已实际读取素材，结果如下。\n{result}'
    client, manager = client_for(tmp_path, engine, lambda prompt, profile, session: result)
    response = client.post('/api/assets/organize?persona=甲', json={})
    assert response.status_code == 202
    job = client.get('/api/assets/organize?persona=甲').json()
    assert (job['status'], job['completed'], job['placed']) == ('done', 1, 1)
    entry, = engine.list_items('甲')
    assert (entry['category'], entry['summary'], entry['tags']) == (
        '城市活动', '南湖社区活动，可用于城市视频', ['纪录片', '社区'])
    assert (tmp_path / 'profiles/甲/assets/城市活动/untitled.txt').read_text(encoding='utf-8').startswith('南湖')
    restored = AssetOrganizer(tmp_path / 'jobs', lambda: engine, manager.runner)
    assert restored.current('甲').status == 'done'
    assert client.get(f"/api/assets/organize/{job['id']}?persona=乙").status_code == 404


@pytest.mark.parametrize('bad_plan', ['foreign', 'missing', 'duplicate', 'traversal', 'invalid-json', 'multiple'])
def test_untrusted_ai_plan_cannot_write_outside_scanned_files(tmp_path, library, bad_plan):
    engine, source = library
    outside = tmp_path / 'outside.txt'
    outside.write_text('其他账号', encoding='utf-8')
    valid = plan_for(source / 'untitled.txt')
    variants = {
        'foreign': plan_for(outside),
        'missing': '{"items":[]}',
        'duplicate': json.dumps({'items': json.loads(valid)['items'] * 2}),
        'traversal': plan_for(source / 'untitled.txt', category='../乙'),
        'invalid-json': '我已经全部整理好了。',
        'multiple': valid + '\n' + valid,
    }
    client, _ = client_for(tmp_path, engine, lambda prompt, profile, session: variants[bad_plan])
    client.post('/api/assets/organize?persona=甲', json={})
    result = client.get('/api/assets/organize?persona=甲').json()
    assert result['status'] == 'failed'
    assert engine.list_items('甲') == []
    assert outside.read_text(encoding='utf-8') == '其他账号'


def test_running_job_is_reused_and_restart_reports_interruption(tmp_path, library):
    engine, _ = library
    _, manager = client_for(tmp_path, engine, lambda prompt, profile, session: '')
    first, files = manager.start('甲', False)
    second, duplicate_files = manager.start('甲', False)
    assert second.id == first.id and duplicate_files == []
    assert len(files) == 1
    restored = AssetOrganizer(tmp_path / 'jobs', lambda: engine, manager.runner)
    result = restored.current('甲')
    assert result.status == 'failed' and '重启' in result.error


def test_agent_failure_is_visible_without_baseline_success(tmp_path, library):
    engine, _ = library
    def unavailable(prompt, profile, session):
        raise HTTPException(502, '模型连接失败')
    client, _ = client_for(tmp_path, engine, unavailable)
    client.post('/api/assets/organize?persona=甲', json={})
    result = client.get('/api/assets/organize?persona=甲').json()
    assert (result['status'], result['error'], result['placed']) == ('failed', '模型连接失败', 0)
    assert engine.list_items('甲') == []


def test_another_live_service_can_read_progress_without_cancelling_job(tmp_path, library, monkeypatch):
    engine, _ = library
    _, manager = client_for(tmp_path, engine, lambda prompt, profile, session: '')
    job, _ = manager.start('甲', False)
    manager._save(job.model_copy(update={'owner_pid': job.owner_pid + 1000}))
    observer = AssetOrganizer(tmp_path / 'jobs', lambda: engine, manager.runner)
    monkeypatch.setattr('web.asset_organization.os.kill', lambda pid, signal: None)
    assert observer.current('甲').status == 'running'


def test_dry_run_understands_content_without_writing_library(tmp_path, library):
    engine, source = library
    client, _ = client_for(tmp_path, engine, lambda prompt, profile, session: plan_for(source / 'untitled.txt'))
    client.post('/api/assets/organize?persona=甲', json={'dry_run': True})
    result = client.get('/api/assets/organize?persona=甲').json()
    assert (result['status'], result['placed'], result['dry_run']) == ('done', 1, True)
    assert engine.list_items('甲') == []


def test_failed_later_write_keeps_progress_of_committed_files(tmp_path, library, monkeypatch):
    engine, source = library
    (source / 'second.txt').write_text('第二份素材', encoding='utf-8')
    items = [json.loads(plan_for(source / name))['items'][0] for name in ['untitled.txt', 'second.txt']]
    original = engine.organize
    def fail_second(profile, *, dry_run, plan):
        if len(engine.list_items(profile)) == 1:
            raise OSError('index unavailable')
        return original(profile, dry_run=dry_run, plan=plan)
    monkeypatch.setattr(engine, 'organize', fail_second)
    client, _ = client_for(tmp_path, engine, lambda prompt, profile, session: json.dumps({'items': items}))
    client.post('/api/assets/organize?persona=甲', json={})
    result = client.get('/api/assets/organize?persona=甲').json()
    assert (result['status'], result['completed'], result['placed']) == ('failed', 1, 1)
    assert len(engine.list_items('甲')) == 1
