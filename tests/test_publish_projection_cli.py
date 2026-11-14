import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_publish_log_cli_replay_preserves_one_run_record(tmp_path):
    target = tmp_path / 'publish-log.json'
    script = ROOT / 'skills' / 'builtin' / 'skill-publish-log' / 'scripts' / 'log.py'
    command = [sys.executable, str(script), '--data', str(target), 'record', '--platform', '小红书',
               '--title', 'A verified post', '--profile', 'alice', '--run-id', 'stable-run']
    for _attempt in range(2):
        subprocess.run(command, cwd=ROOT, capture_output=True, check=True, timeout=15)
    stored = json.loads(target.read_text())
    assert [(entry['run_id'], entry['profile']) for entry in stored['entries']] == [('stable-run', 'alice')]


def test_calendar_cli_replay_preserves_one_run_record(tmp_path):
    target = tmp_path / 'schedule.json'
    script = ROOT / 'skills' / 'shared' / 'scripts' / 'calendar_ops.py'
    command = [sys.executable, str(script), '--data', str(target), 'record-publish', '--platform', 'xiaohongshu',
               '--title', 'A verified post', '--profile', 'alice', '--run-id', 'stable-run', '--no-log']
    for _attempt in range(2):
        subprocess.run(command, cwd=ROOT, capture_output=True, check=True, timeout=15)
    stored = json.loads(target.read_text())
    assert [(entry['run_id'], entry['profile']) for entry in stored] == [('stable-run', 'alice')]
