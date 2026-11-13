# Hypit video replication parity

This page describes the opt-in Hypit path in Easel. OpenFilm remains the
default video engine. A Hypit run is accepted only when its engine, profile,
session, provider, and output boundary are explicit.

## Boundaries

The flow has four independently testable boundaries.

| Boundary | Contract | Durable data |
| --- | --- | --- |
| Source | `easel.hypit_security` validates an HTTP(S) URL, exact host allowlist, DNS result, redirects, and media caps. | Reference evidence, source checksum, rights, and attribution |
| Adapter | `skills/shared/scripts/hypit.py` receives absolute project, asset, output, and optional report roots. It requires the pinned `@hypit/hypit` version and maps provider status to Easel states. | Redacted runtime metadata and logs |
| Run | `easel.hypit_lifecycle.HypitLifecycle` persists plan, run, build, result, checkpoint, retry, and cancellation transitions. | `outputs/_hypit/runs/<run-id>/lifecycle.json` and derived files |
| Artifact | `easel.video_artifact_boundary` imports only checksummed derived artifacts into `outputs/derived/<profile>/<kind>`. Native `.hypit`, `.svml`, `.svs`, `.svrun`, `node_modules`, and `.git` trees stay engine-scoped. | `ArtifactRecord` with source engine, target profile, checksum, and attribution |

OpenFilm compatibility is deliberately one-way at this boundary. Existing
OpenFilm `film.html` projects, profile assets, and session pointers remain
readable. Hypit does not rewrite a film project or turn a Hypit report into
`film.html`.

## Run manifest and states

`VideoRunManifest` is version 1 and is independent from OpenFilm `.easel.json`.
It requires distinct `run_id`, `attempt_id`, and `build_id` values, an engine
of `hypit` or `openfilm`, and a project root under
`profiles/<profile>/sessions/<session>/video/<run>`.

The manifest records `checkpoint`, `outputs`, `reports`, `provenance`, and
`provider_policy`. Paths are relative to the assigned project root and runtime
directories are rejected. A manifest is written atomically as
`run-manifest.json`.

Lifecycle states are `planned`, `running`, `built`, `succeeded`, `failed`, and
`cancelled`. A normal run follows `planned → running → built → succeeded`.
Failures and cancellations are terminal until an explicit retry returns to
`planned` and creates a new attempt. A succeeded run is idempotent on rerun.
Checkpoints carry schema 1, a monotonic revision, stage, and input fingerprint.
The fingerprint prevents a retry from applying a checkpoint from another
source, profile, or session.

## Source and resource limits

Set `EASEL_VIDEO_REFERENCE_HOSTS` to an exact comma-separated hostname list.
The default is empty. Every initial and redirected URL is revalidated. URL
credentials, non-HTTP schemes, unallowlisted hosts, private, loopback,
link-local, multicast, unspecified, and reserved DNS results are rejected.
Redirects are capped at three and downloads at 1,000,000,000 bytes. Video
metadata is capped at 20 minutes and 1920×1080, with the same byte cap for
decompressed data. These checks run before a provider adapter receives the
source.

The lifecycle caps one active Build per session, two active Builds per profile,
and a timeout of at most two hours. Retention cleanup preserves active,
shared, approved, and audit-pinned artifacts. Expired unpinned files are
removed only when age or aggregate-byte limits require cleanup.

## Provider, pricing, and license gates

`ProviderPolicy` stores provider and model identifiers, a route (`local`,
`byok`, or `hypihub`), a scoped credential reference, pricing receipt, user
approval, and source provenance. Secret values never enter a manifest. Metadata
is recursively redacted before serialization.

Local routes are ready without a paid receipt. BYOK and HypiHub routes require
a credential reference in the same profile and session. Paid sample or full
Builds require a non-expired pricing receipt and explicit approval. An approval
with `scope=sample` cannot authorize a full Build.

The replication license is local or single-organization only. Set
`EASEL_HYPIT_STUDIO_LICENSE=enabled` and retain the attribution
`Hypit replication powered by Easel` in derived output provenance. Commercial
or multi-tenant use, a missing license, or missing attribution is rejected.

## Recovery, migration, and rollback

Every lifecycle transition and checkpoint is persisted before the next stage.
On a failed run, inspect `lifecycle.json`, preserve the last valid checkpoint,
and call the rerun endpoint only after the source fingerprint and provider
receipt are still valid. A cancelled run is safe to retry and a succeeded run
returns its existing result without starting another provider process.

Use `migration_with_rollback` for schema changes. It snapshots the assigned
root, runs the migration, writes a migration marker only after success, and
restores the snapshot if the migration raises. Keep the old manifest and native
engine namespace until the new manifest has been read back successfully.

## Studio handshake and session isolation

Configure `EASEL_HYPIT_STUDIO_URL` and exact scheme-qualified
`EASEL_HYPIT_STUDIO_ORIGINS`. Wildcards, paths, credentials, and fragments are
ignored. Easel issues a short-lived HS256 lease containing the run, profile,
session, engine `hypit`, source `easel`, expiry, nonce, and one-shot `jti`.

`GET /api/hypit/studio` returns a URL only for a ready run, an allowlisted
Studio origin, and an available local license. The iframe sends a handshake
with its token and nonce. Easel checks the request `Origin`, run/profile/session
scope, engine/source identity, license, expiry, signature, and nonce. A token
is consumed on success, so replay, wrong-session, wrong-origin, and stale-token
requests fail with 403.

## Compatibility fixture and deterministic e2e

`tests/fixtures/hypit_reference.json` is a small synthetic reference with ASR,
dimensions, rights, and attribution. `tests/fixtures/hypit_adapter_double.py`
uses that record to write tiny deterministic sample/full payloads, a report, and
a scoped `VideoRunManifest`. It does not contact a provider or read a secret.

`tests/test_hypit_video_replication_e2e.py` exercises the real FastAPI routes
and lifecycle stores for the following sequence.

1. Fetch an allowlisted HTTP fixture and reject an unallowlisted URL.
2. Plan, analyze, and complete a sample run.
3. Create a variant with reference evidence and approve its sample fingerprint.
4. Reject a paid full Build without pricing, then plan and complete the paid full Build.
5. Read the report and artifact endpoints, import a checksummed MP4, and read the manifest.
6. Rerun the succeeded run idempotently.
7. Reuse an existing OpenFilm project, profile asset, and session pointer.
8. Accept one Studio handshake, then reject replayed and wrong-session requests.
9. Capture missing ASR, timeout, and cancellation outcomes.

## Verification commands

Run the full Python suite from the Easel root.

```bash
PYTHONPATH=. uv run --with pytest pytest -q
```

Run the focused synthetic e2e and frontend production checks.

```bash
PYTHONPATH=. uv run --with pytest pytest -q tests/test_hypit_video_replication_e2e.py
(cd web/frontend && pnpm build)
(cd web/frontend && pnpm lint)
node --test tests/frontend/hypitWorkspace.test.mjs tests/frontend/videoAccountScope.test.mjs tests/frontend/openfilmContext.test.mjs
```

When the OpenFilm submodule is initialized, run its exact workspace checks.

```bash
(cd subprojects/openfilm && pnpm typecheck)
(cd subprojects/openfilm && pnpm build)
(cd subprojects/openfilm && pnpm --filter openfilm test)
(cd subprojects/openfilm && pnpm --filter @openfilm/desktop test)
```

The OpenFilm component/browser harness is exercised by
`tests/frontend/helpers/openfilmHarness.mjs` through
`tests/frontend/openfilmContext.test.mjs`. These checks use synthetic DOM and
wire fixtures and do not require a logged-in browser or private media.
