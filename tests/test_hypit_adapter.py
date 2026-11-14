import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/shared/scripts"))
import hypit


class HypitAdapterTests(unittest.TestCase):
    def project(self, version="0.3.2"):
        root = Path(tempfile.mkdtemp())
        (root / "assets").mkdir()
        (root / "package.json").write_text(json.dumps({"dependencies": {"@hypit/hypit": version}}))
        (root / ".node-version").write_text("24.14.1\n")
        return root

    def test_explicit_roots_and_version_rejection(self):
        root = self.project()
        cfg = hypit.HypitConfig(root, root / "assets", root / "outputs")
        self.assertEqual(cfg.project, root)
        bad = self.project("latest")
        result = hypit.readiness(hypit.HypitConfig(bad, bad / "assets", bad / "outputs"))
        self.assertFalse(result["package"]["pinned"])

    def test_redacts_secrets_and_maps_failures(self):
        redacted = hypit._redact({"api_key": "abc", "nested": "Bearer secret"})
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertNotIn("secret", redacted["nested"])
        self.assertEqual(hypit.translate_status("timeout", timed_out=True), "failed")
        self.assertEqual(hypit.translate_status("cancelled"), "cancelled")

    def test_run_redacts_untrusted_runtime_output_everywhere(self):
        root = self.project()
        executable = root / "fake-hypit"
        executable.write_text(
            "#!/bin/sh\n"
            "printf '%s\n' 'render complete token=LEAK password:LEAK2 api_key=LEAK3 Bearer LEAK4'\n"
        )
        executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        result = hypit.run(hypit.HypitConfig(root, root / "assets", root / "outputs", executable=executable))
        serialized_result = json.dumps(result, sort_keys=True)
        self.assertIn("render complete", result["stdout"])
        self.assertIn("token=[REDACTED]", result["stdout"])
        for secret in ("LEAK", "LEAK2", "LEAK3", "LEAK4"):
            self.assertNotIn(secret, serialized_result)

        project_root = root / "profiles" / "p" / "sessions" / "s" / "video" / "run"
        project_root.mkdir(parents=True)
        manifest = hypit.manifest_for_status(
            {"run_id": "r", "attempt_id": "a", "build_id": "b", "profile_id": "p",
             "session_id": "s", "project_root": project_root,
             "checkpoint": {"runtime_stdout": result["stdout"]}},
            "succeeded",
        )
        manifest_path = project_root / "run-manifest.json"
        manifest.write(manifest_path)
        log_path = project_root / "runtime.log"
        log_path.write_text(serialized_result, encoding="utf-8")
        for path in (manifest_path, log_path):
            contents = path.read_text(encoding="utf-8")
            for secret in ("LEAK", "LEAK2", "LEAK3", "LEAK4"):
                self.assertNotIn(secret, contents)

    def test_missing_executable_is_typed(self):
        root = self.project()
        with self.assertRaises(hypit.HypitMissingExecutableError):
            hypit.run(hypit.HypitConfig(root, root / "assets", root / "outputs"))

    def test_manifest_translation_keeps_hypit_engine(self):
        root = self.project()
        project = root / "profiles" / "p" / "sessions" / "s" / "video" / "run"
        project.mkdir(parents=True)
        manifest = hypit.manifest_for_status({"run_id": "r", "attempt_id": "a", "build_id": "b",
            "profile_id": "p", "session_id": "s", "project_root": project}, "succeeded")
        self.assertEqual(manifest.engine, "hypit")
        self.assertEqual(manifest.state, "succeeded")
        self.assertEqual(manifest.provenance["version"], "0.3.2")


def test_readiness_accepts_supported_node_without_project_pin(tmp_path: Path):
    from unittest.mock import patch

    (tmp_path / 'assets').mkdir()
    (tmp_path / 'package.json').write_text('{"dependencies":{"@hypit/hypit":"0.3.2"}}')
    executable = tmp_path / 'hypit'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    versions = {'node': 'v24.22.1', 'pnpm': '10.33.0', 'ffmpeg': 'ffmpeg version 8', 'google-chrome': 'Chrome 140'}
    with patch.object(hypit, '_which', side_effect=lambda name: name), patch.object(
        hypit, '_probe', side_effect=lambda path, args: (True, versions[path])
    ):
        result = hypit.readiness(hypit.HypitConfig(tmp_path, tmp_path / 'assets', tmp_path / 'outputs', executable=executable))
    assert result['requirements']['node']['ok'] is True
    assert result['ok'] is True


def test_readiness_uses_shared_runtime_and_macos_chrome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import easel.browser

    project = tmp_path / 'project'
    assets = project / 'assets'
    assets.mkdir(parents=True)
    runtime = tmp_path / 'runtime'
    binaries = runtime / 'node_modules' / '.bin'
    binaries.mkdir(parents=True)
    (runtime / 'package.json').write_text('{"dependencies":{"@hypit/hypit":"0.3.2"}}')
    for name, version in [('node', 'v24.22.1'), ('pnpm', '10.33.0'), ('ffmpeg', 'ffmpeg 8'), ('hypit', '0.3.2'), ('mac-chrome', 'Chrome 157')]:
        path = binaries / name
        path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{version}"\n')
        path.chmod(0o755)
    monkeypatch.setenv('PATH', str(binaries))
    monkeypatch.setattr(easel.browser.sys, 'platform', 'darwin')
    monkeypatch.setattr(easel.browser, '_candidates', lambda name: [binaries / 'mac-chrome'])
    result = hypit.readiness(hypit.HypitConfig(project, assets, project / 'outputs', runtime_root=runtime))
    assert result['ok'] is True
    assert result['executable']['path'] == str(binaries / 'hypit')
    assert result['requirements']['chrome']['detail'] == 'Chrome 157'
    assert result['requirements']['pnpm']['detail'] == '10.33.0'
    (project / 'package.json').write_text('{"dependencies":{"@hypit/hypit":"latest"}}')
    assert hypit.readiness(hypit.HypitConfig(project, assets, project / 'outputs', runtime_root=runtime))['ok'] is False


def test_integrity_belongs_to_hypit_package(tmp_path: Path):
    (tmp_path / 'package-lock.json').write_text(json.dumps({'packages': {
        'node_modules/unrelated': {'integrity': 'sha512-unrelated'},
        'node_modules/@hypit/hypit': {'integrity': 'sha512-hypit'},
    }}))
    assert hypit._integrity(tmp_path) == 'sha512-hypit'


if __name__ == "__main__":
    unittest.main()
