"""The CLI entry and integration modules must operate on the same FastAPI app."""
from pathlib import Path
import subprocess
import sys


def test_module_cli_startup_reuses_canonical_app_and_generation_state():
    script = '''
import runpy
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import uvicorn
from fastapi.testclient import TestClient

def serve(application, **kwargs):
    module = sys.modules['web.app']
    assert application is module.app
    with tempfile.TemporaryDirectory() as directory:
        outputs = Path(directory) / 'outputs'
        outputs.mkdir()
        env = Path(directory) / '.env'
        env.write_text('')
        with patch.multiple(module, OUTPUTS_DIR=outputs, ENV_FILE=env, SESSIONS_DIR=outputs / '_sessions'), patch.object(module, 'profile_exists', lambda name: name == 'alice'):
            with TestClient(application, base_url='http://localhost:7860', client=('127.0.0.1', 4000)) as client:
                response = client.get('/api/ai/logs?persona=alice')
                assert response.status_code == 200 and response.json()['total'] == 0, response.text
                assert module.app.state.generation_tasks is not None
    print('canonical main startup passed')

uvicorn.run = serve
runpy.run_module('web.app', run_name='__main__', alter_sys=True)
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'canonical main startup passed' in result.stdout
