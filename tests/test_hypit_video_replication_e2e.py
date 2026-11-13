from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import easel.hypit_api as hypit_api
import web.app as web
from easel.hypit_lifecycle import HypitLifecycle
from easel.hypit_security import ReferenceURLBlocked, fetch_reference
from easel.hypit_studio import _USED
from easel.hypit_variants import HypitVariantStore
from easel.video_provider_policy import CredentialRef, ProviderPolicy, ProviderPolicyError, ProviderRef
from easel.video_run_manifest import read_openfilm_session_pointer


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "tests" / "fixtures" / "hypit_reference.json"
sys.path.insert(0, str(REFERENCE.parent))
from hypit_adapter_double import DeterministicHypitAdapter  # noqa: E402


class _FixtureHandler(BaseHTTPRequestHandler):
    payload = REFERENCE.read_bytes()

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/reference.json":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *_args: object) -> None:
        return


def _public_resolver(*_args: object, **_kwargs: object):
    return [(None, None, None, None, ("93.184.216.34", 0))]


@pytest.fixture
def e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    profile_assets = workspace / "profiles" / "profile-a" / "assets"
    profile_assets.mkdir(parents=True)
    (profile_assets / "asset.txt").write_text("synthetic asset\n", encoding="utf-8")
    openfilm = workspace / "outputs" / "existing-openfilm"
    openfilm.mkdir(parents=True)
    (openfilm / "film.html").write_text("<section>existing project</section>", encoding="utf-8")
    sessions = workspace / "outputs" / "_sessions"
    sessions.mkdir(parents=True)
    (sessions / "session-a.openfilm-topic.txt").write_text("existing-openfilm\n", encoding="utf-8")

    hypit_root = workspace / "outputs" / "_hypit"
    fixture = json.loads(REFERENCE.read_text(encoding="utf-8"))
    adapter = DeterministicHypitAdapter(hypit_root / "runs", workspace, fixture)
    monkeypatch.setattr(hypit_api, "ROOT", hypit_root)
    monkeypatch.setattr(hypit_api, "LIFECYCLE", HypitLifecycle(hypit_root / "runs", runner=adapter))
    monkeypatch.setattr(hypit_api, "VARIANTS", HypitVariantStore(hypit_root / "variants"))
    monkeypatch.setenv("EASEL_VIDEO_REFERENCE_HOSTS", "127.0.0.1")
    monkeypatch.setenv("EASEL_HYPIT_STUDIO_ORIGINS", "http://localhost:7860,http://localhost:4123")
    monkeypatch.setenv("EASEL_HYPIT_STUDIO_URL", "http://localhost:4123/studio")
    monkeypatch.setenv("EASEL_HYPIT_STUDIO_LICENSE", "enabled")
    _USED.clear()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    local = "http://127.0.0.1:%d/reference.json" % server.server_port
    client = TestClient(web.app, base_url="http://localhost:7860", headers={"Origin": "http://localhost:7860"})
    yield workspace, local, client, adapter
    client.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_allowlisted_reference_to_full_build_manifest_artifact_and_studio_rerun(e2e):
    workspace, reference_url, client, adapter = e2e
    fixture = json.loads(fetch_reference(reference_url, resolver=_public_resolver))
    assert fixture["source_id"] == "synthetic-reference"
    with pytest.raises(ReferenceURLBlocked, match="not allowlisted"):
        fetch_reference(reference_url.replace("127.0.0.1", "evil.example"), resolver=_public_resolver)

    scope = {"profile_id": "profile-a", "session_id": "session-a"}
    fingerprint = "a" * 64
    checkpoint = {"revision": 0, "schema": 1, "stage": "sample", "fingerprint": fingerprint}
    pricing = client.get("/api/hypit/pricing", params={**scope, "engine": "hypit", "kind": "sample"})
    assert pricing.status_code == 200
    sample_plan = client.post("/api/hypit/runs", json={**scope, "kind": "sample", "pricing": pricing.json(), "checkpoint": checkpoint, "input_fingerprint": fingerprint})
    assert sample_plan.status_code == 200
    sample_id = sample_plan.json()["id"]
    assert client.post(f"/api/hypit/runs/{sample_id}/analysis", json={**scope, "analysis": {"asr": fixture["asr"], "source_id": fixture["source_id"]}}).status_code == 200
    sample_done = client.post(f"/api/hypit/runs/{sample_id}/run", json=scope)
    assert sample_done.status_code == 200 and sample_done.json()["state"] == "succeeded"

    variant = client.post("/api/hypit/variants", json={
        **scope,
        "variant_id": "v-synthetic",
        "brief": {"title": fixture["title"]},
        "treatment": {"style": "documentary"},
        "analysis": {"asr": fixture["asr"]},
        "timeline": {"duration_seconds": fixture["duration_seconds"]},
        "sample_target": {"kind": "sample", "duration_seconds": 8},
        "full_target": {"kind": "full", "duration_seconds": 8},
        "selected_anchors": ["anchor-1"],
        "selected_assets": ["profiles/profile-a/assets/asset.txt"],
        "reference_evidence": [{"source_id": fixture["source_id"], "rights": fixture["rights"], "attribution": fixture["attribution"]}],
    })
    assert variant.status_code == 200 and variant.json()["approval_state"] == "pending"
    assert client.post("/api/hypit/variants/v-synthetic/approve", json=scope).status_code == 200
    assert client.post("/api/hypit/variants/v-synthetic/approve", json={"profile_id": "other", "session_id": "session-a"}).status_code == 403

    provider = ProviderRef("provider-hypit", "model-fixture", "http://fixture.invalid", "profile-a", "session-a", route="hypihub", credential_id="cred-fixture")
    credential = CredentialRef("cred-fixture", "profile-a", "session-a", "HYPIT_FIXTURE_KEY")
    with pytest.raises(ProviderPolicyError, match="pricing"):
        ProviderPolicy(provider, credential=credential).assert_build_allowed()

    full_fingerprint = "b" * 64
    full_pricing = client.get("/api/hypit/pricing", params={**scope, "engine": "hypit", "kind": "full"})
    full_plan = client.post("/api/hypit/runs", json={**scope, "kind": "full", "pricing": full_pricing.json(), "checkpoint": {"revision": 0, "schema": 1, "stage": "full", "fingerprint": full_fingerprint}, "input_fingerprint": full_fingerprint})
    assert full_plan.status_code == 200
    full_id = full_plan.json()["id"]
    assert client.post(f"/api/hypit/runs/{full_id}/analysis", json={**scope, "analysis": {"asr": fixture["asr"], "variant_id": "v-synthetic"}}).status_code == 200
    assert client.post(f"/api/hypit/runs/{full_id}/timeline", json={**scope, "timeline": {"duration_seconds": 8, "variant_id": "v-synthetic"}}).status_code == 200
    done = client.post(f"/api/hypit/runs/{full_id}/run", json=scope)
    assert done.status_code == 200 and done.json()["state"] == "succeeded"

    reports = client.get(f"/api/hypit/runs/{full_id}/reports", params=scope)
    artifacts = client.get(f"/api/hypit/runs/{full_id}/artifacts", params=scope)
    assert reports.status_code == 200 and reports.json()["reports"][0]["state"] == "succeeded"
    assert artifacts.status_code == 200 and {item["name"] for item in artifacts.json()["artifacts"]} == {"final.mp4", "report.json"}
    imported = client.post(f"/api/hypit/runs/{full_id}/artifacts/import", json={**scope, "run_id": full_id, "artifact": "final.mp4"})
    assert imported.status_code == 200 and imported.json()["imported"] is True

    manifest = workspace / "profiles" / "profile-a" / "sessions" / "session-a" / "video" / full_id / "run-manifest.json"
    assert manifest.is_file() and json.loads(manifest.read_text(encoding="utf-8"))["engine"] == "hypit"
    assert (workspace / "outputs" / "derived" / "profile-a" / "mp4" / "final.mp4").read_bytes().startswith(b"synthetic-full")
    assert client.post(f"/api/hypit/runs/{full_id}/rerun", json=scope).json()["state"] == "succeeded"
    assert read_openfilm_session_pointer(workspace, "session-a") == workspace / "outputs" / "existing-openfilm"
    assert (workspace / "profiles" / "profile-a" / "assets" / "asset.txt").is_file()

    studio = client.get("/api/hypit/studio", params={**scope, "run_id": full_id})
    assert studio.status_code == 200 and studio.json()["available"] is True
    lease = studio.json()
    handshake_payload = {**scope, "run_id": full_id, "token": lease["token"], "nonce": lease["nonce"], "action": "handshake", "engine": "hypit", "source": "easel"}
    accepted = client.post("/api/hypit/studio/handshake", json=handshake_payload, headers={"Origin": "http://localhost:7860"})
    assert accepted.status_code == 200 and accepted.json()["accepted"] is True
    assert client.post("/api/hypit/studio/handshake", json=handshake_payload, headers={"Origin": "http://localhost:7860"}).status_code == 403
    assert client.get("/api/hypit/studio", params={"profile_id": "profile-a", "session_id": "wrong-session", "run_id": full_id}).status_code == 403
    assert [call["kind"] for call in adapter.calls] == ["sample", "full"]


def test_missing_asr_timeout_and_cancel_are_captured(e2e):
    _workspace, _reference_url, client, _adapter = e2e
    scope = {"profile_id": "profile-a", "session_id": "session-a"}
    fingerprint = "c" * 64
    pricing = client.get("/api/hypit/pricing", params={**scope, "engine": "hypit"}).json()
    planned = client.post("/api/hypit/runs", json={**scope, "pricing": pricing, "input_fingerprint": fingerprint, "checkpoint": {"revision": 0, "schema": 1, "stage": "sample", "fingerprint": fingerprint}}).json()
    missing = client.post(f"/api/hypit/runs/{planned['id']}/run", json=scope).json()
    assert missing["state"] == "failed" and "missing ASR" in missing["error"]

    timeout_adapter = DeterministicHypitAdapter(Path(_workspace / "timeout-runs"), _workspace, {"source_id": "fixture", "rights": "fixture-only", "attribution": "fixture"}, mode="timeout")
    timeout_service = HypitLifecycle(_workspace / "timeout-state", runner=timeout_adapter, timeout_seconds=1)
    timeout_rec = timeout_service.plan("profile-a", "timeout-session", pricing=timeout_service.pricing("profile-a"))
    assert timeout_service.run(timeout_rec.id).state == "failed"

    cancel_adapter = DeterministicHypitAdapter(Path(_workspace / "cancel-runs"), _workspace, {"source_id": "fixture", "rights": "fixture-only", "attribution": "fixture"}, mode="cancel")
    cancel_service = HypitLifecycle(_workspace / "cancel-state", runner=cancel_adapter, timeout_seconds=5)
    cancel_rec = cancel_service.plan("profile-a", "cancel-session", pricing=cancel_service.pricing("profile-a"))
    assert cancel_service.cancel(cancel_rec.id).state == "cancelled"
