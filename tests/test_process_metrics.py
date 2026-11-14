"""Real resource observations, unavailable-platform behavior and mounted exclusions."""
from pathlib import Path
from subprocess import CompletedProcess
import gc
import os
import sys
import time
import pytest
from easel import process_metrics as process


def scalars(lines: list[str]) -> dict[str, float]:
    return {name: float(value) for line in lines if not line.startswith('#') and '{' not in line
            for name, value in [line.rsplit(' ', 1)]}


def test_real_process_cpu_memory_start_and_gc_are_observed() -> None:
    before = scalars(process.render_process_metrics())
    until = time.process_time() + 0.03
    while time.process_time() < until:
        sum(range(1000))
    allocated = bytearray(32 * 1024 * 1024)
    allocated[0] = 1
    gc.collect()
    lines = process.render_process_metrics()
    after = scalars(lines)
    assert after['process_cpu_seconds_total'] > before['process_cpu_seconds_total'] + 0.02
    assert after['process_cpu_user_seconds_total'] >= before['process_cpu_user_seconds_total']
    assert after['process_cpu_system_seconds_total'] >= before['process_cpu_system_seconds_total']
    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        assert after['process_resident_memory_bytes'] > len(allocated)
        assert after['process_resident_memory_bytes'] > before['process_resident_memory_bytes'] + 8 * 1024 * 1024
        assert after['process_virtual_memory_bytes'] >= after['process_resident_memory_bytes']
        assert 0 < after['process_start_time_seconds'] <= time.time()
        assert after['process_start_time_seconds'] == before['process_start_time_seconds']
        assert after['process_uptime_seconds'] >= before['process_uptime_seconds']
        assert after['process_peak_resident_memory_bytes'] >= before['process_peak_resident_memory_bytes']
    assert any('python_info{implementation=' in line for line in lines)
    assert any(line.startswith('python_gc_collections_total{generation="2"}') and float(line.rsplit(' ', 1)[1]) > 0 for line in lines)


def test_linux_parser_handles_spaces_and_parentheses_in_command(monkeypatch) -> None:
    monkeypatch.setattr(os, 'sysconf', lambda name: 4096 if name == 'SC_PAGE_SIZE' else 100)
    fields = ['S'] + ['0'] * 18 + ['250'] + ['0'] * 8
    result = process.linux_snapshot('123 (a tricky ) name) ' + ' '.join(fields), '1000 200 10 0 0 0 0', 'cpu 1 2 3\nbtime 1700000000\n')
    assert result == process.ProcessSnapshot(200 * 4096, 1000 * 4096, 1700000002.5)


def test_restricted_procfs_uses_posix_stats_without_fabricating_values(monkeypatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(os, 'name', 'posix')
    def denied(path):
        raise PermissionError('restricted procfs')
    monkeypatch.setattr(Path, 'read_text', denied)
    def ps(command, **options):
        assert command == ['ps', '-p', str(os.getpid()), '-o', 'rss=', '-o', 'vsz=', '-o', 'lstart=']
        assert options['timeout'] == 1 and options['env']['LC_ALL'] == 'C'
        return CompletedProcess(command, 0, '  1234 5678 Sat Nov 14 08:09:10 2026\n', '')
    monkeypatch.setattr(process.subprocess, 'run', ps)
    snapshot = process.process_snapshot()
    assert snapshot.resident_bytes == 1234 * 1024
    assert snapshot.virtual_bytes == 5678 * 1024
    assert snapshot.start_seconds > 0


def test_unavailable_os_values_are_omitted_not_zero(monkeypatch) -> None:
    monkeypatch.setattr(process, 'process_snapshot', lambda: process.ProcessSnapshot())
    monkeypatch.setattr(process, 'peak_resident_bytes', lambda: None)
    values = scalars(process.render_process_metrics())
    assert 'process_cpu_seconds_total' in values
    assert not {'process_resident_memory_bytes', 'process_virtual_memory_bytes',
                'process_peak_resident_memory_bytes', 'process_start_time_seconds', 'process_uptime_seconds'} & values.keys()


@pytest.mark.parametrize('failure', ['unavailable', 'malformed'])
def test_posix_fallback_failure_omits_observations(monkeypatch, failure: str) -> None:
    monkeypatch.setattr(os, 'name', 'posix')
    def failed(command, **options):
        if failure == 'unavailable':
            raise FileNotFoundError('ps unavailable')
        return CompletedProcess(command, 0, 'invalid', '')
    monkeypatch.setattr(process.subprocess, 'run', failed)
    assert process.posix_snapshot() == process.ProcessSnapshot()


def test_mounted_scrapes_and_health_do_not_account_themselves(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from web import app as web
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    metrics = web.app.state.http_metrics
    with TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 1234)) as client:
        before = dict(metrics.counts)
        inflight = dict(metrics.inflight)
        for _ in range(2):
            response = client.get('/metrics?probe=synthetic-private')
            assert response.status_code == 200
            assert 'process_cpu_seconds_total' in response.text and 'python_gc_collections_total' in response.text
            assert 'synthetic-private' not in response.text
            client.get('/health')
        assert dict(metrics.counts) == before
        assert dict(metrics.inflight) == inflight
        assert client.get('/missing-process-metric-check').status_code == 404
        assert sum(metrics.counts.values()) == sum(before.values()) + 1
