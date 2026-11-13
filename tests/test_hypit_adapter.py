import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
