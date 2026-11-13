"""Deterministic Hypit provider double used by the replication e2e test.

The double writes tiny text payloads with media-like names.  It never contacts a
provider, reads credentials, or emits native runtime files as public artifacts.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Mapping

from easel.hypit_lifecycle import LifecycleError
from easel.video_run_manifest import VideoRunManifest, resolve_project_root


class DeterministicHypitAdapter:
    """A small runner that exercises sample/full, timeout, and cancellation paths."""

    def __init__(self, runs_root: Path, workspace_root: Path, fixture: Mapping[str, Any], mode: str = "success") -> None:
        self.runs_root = Path(runs_root)
        self.workspace_root = Path(workspace_root)
        self.fixture = dict(fixture)
        self.mode = mode
        self.calls: list[dict[str, Any]] = []

    def __call__(self, record: Any, checkpoint: Mapping[str, Any], cancel: threading.Event) -> dict[str, Any]:
        self.calls.append({"run_id": record.id, "kind": record.kind, "checkpoint": dict(checkpoint)})
        if self.mode == "timeout":
            cancel.wait(2)
            return {"outputs": []}
        if self.mode == "cancel":
            cancel.wait(2)
            raise LifecycleError("cancelled")
        analysis = checkpoint.get("analysis")
        if not isinstance(analysis, Mapping) or not analysis.get("asr"):
            raise LifecycleError("missing ASR analysis")

        run_root = self.runs_root / record.id
        run_root.mkdir(parents=True, exist_ok=True)
        target = "sample" if record.kind == "sample" else "full"
        media_name = "sample.mp4" if target == "sample" else "final.mp4"
        report_name = "sample-report.json" if target == "sample" else "report.json"
        media = run_root / media_name
        report = run_root / report_name
        media.write_bytes(f"synthetic-{target}-video\n".encode("utf-8"))
        report.write_text(json.dumps({"target": target, "source_id": self.fixture["source_id"], "asr": analysis["asr"]}, sort_keys=True) + "\n", encoding="utf-8")

        project_root = resolve_project_root(self.workspace_root, record.profile_id, record.session_id, record.id)
        public_media = project_root / "outputs" / target / media_name
        public_report = project_root / "reports" / report_name
        public_media.parent.mkdir(parents=True, exist_ok=True)
        public_report.parent.mkdir(parents=True, exist_ok=True)
        public_media.write_bytes(media.read_bytes())
        public_report.write_bytes(report.read_bytes())
        manifest = VideoRunManifest.create(
            run_id=record.id,
            attempt_id=f"attempt-{record.attempt}",
            build_id=f"build-{record.id}",
            engine="hypit",
            profile_id=record.profile_id,
            session_id=record.session_id,
            project_root=project_root,
            state="succeeded",
            checkpoint={"stage": target, "source_id": self.fixture["source_id"]},
            outputs=[f"outputs/{target}/{media_name}"],
            reports=[f"reports/{report_name}"],
            provenance={"source_id": self.fixture["source_id"], "rights": self.fixture["rights"], "attribution": self.fixture["attribution"]},
            provider_policy={"engine": "hypit", "route": "local", "paid": target == "full"},
        )
        manifest.write(project_root / "run-manifest.json")
        return {
            "checkpoint": {"stage": target},
            "outputs": [str(media), str(report)],
            "logs": [f"deterministic {target} output", "manifest written"],
        }


__all__ = ["DeterministicHypitAdapter"]
