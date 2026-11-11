# GitHub App integration — frontend and LLM handoff (D4)

**Scope rule:** This implementation changes files under `backend/` only. The `frontend/`, `llm/`, `scraper/`, root README, root Dockerfile, and `.github/` workflow files in this ZIP are exactly as they were in the uploaded merged branch. This document lives inside `backend/docs/` so it can be committed without modifying another team's area.

## What is ready in the backend

- GitHub App installation access using App ID + RSA private key, and short-lived installation tokens.
- Admin-protected installation listing, authorized repository listing, repository selection, and configurable JSON file path.
- Strictly validated `schema_version: 1` JSON product-feature manifest, maximum 128 KiB. Only an authorized repository/file is read.
- Successful syncs persist a versioned, validated SCADABLE baseline in PostgreSQL; failed fetch/validation retains the previous successful baseline.
- Signed push webhooks on the selected repository's **default branch** enqueue a restart-safe sync; duplicate/unrelated deliveries are ignored. An opt-in background worker processes pending jobs.
- Existing competitor analysis and overall SWOT comparisons retrieve the latest validated baseline from the database; the existing static baseline remains until a successful sync.
- Baseline changes invalidate old overall SWOT comparison results; rebuilding comparisons is **opt-in** because LLM calls cost money.

This is backend integration, **not** proof of live GitHub installation or a finished user-facing repository picker. Real credentials, installation, DB, feature file, and a deployment smoke test are required.

## Frontend teammate — existing code and API wiring

**Current frontend locations (no frontend changes made):**

- `frontend/app/company/page.tsx` renders `offerings` and `positioningNotes`; its GitHub status card is commented out. The comment identifies which imports and markup to restore.
- `frontend/lib/api/company.ts` already calls `GET /company`.
- `frontend/lib/api/types.ts` already defines `CompanyProfile.github` with `connected`, `repository`, `trackedFile`, `statusLabel`, `lastSyncedAt`, and `recentChange`.
- `frontend/lib/api/client.ts` does not currently send authenticated admin requests.

**Public read-only endpoint (existing response shape):** `GET /company`

```json
{
  "offerings": ["Frameworks: SOC 2, ISO 27001", "Automated evidence collection"],
  "positioningNotes": ["Developer-friendly compliance workflows"],
  "github": {
    "connected": true,
    "repository": "example-org/product-docs",
    "trackedFile": "docs/scadable-features.json",
    "statusLabel": "GitHub features synced",
    "lastSyncedAt": "2026-11-09T18:30:00Z",
    "recentChange": "Feature file revision abc1234"
  }
}
```

The JSON above is **illustrative only**, not current SCADABLE production data. `connected` becomes true after a successful sync, not merely after selecting a repository. The API keeps serving the last good profile if a later sync fails; `statusLabel` distinguishes this.

**Management API** (requires `Authorization: Bearer <GITHUB_INTEGRATION_ADMIN_TOKEN>` on a trusted server):

| Method | Path | Purpose | Input |
| --- | --- | --- | --- |
| `GET` | `/github/installations` | List authorized GitHub App installations | — |
| `GET` | `/github/repositories?installation_id=77` | List repositories enabled for the chosen installation | — |
| `GET` | `/github/status` | Selected repo, path, sync status, SHA, error, time | — |
| `POST` | `/github/selection` | Select repository | `{"installationId":77,"repositoryId":101}` |
| `POST` | `/github/file` | Configure versioned JSON document path | `{"filePath":"docs/scadable-features.json"}` |
| `POST` | `/github/sync` | Fetch and validate the current selection; **no LLM calls by default** | — |
| `GET` | `/github/features` | Show the effective structured baseline, with `source` | — |
| `POST` | `/github/sync?recompare=true` | Sync and, if changed, schedule potentially billable overall comparison | — |

Use **camelCase** in management request and response models. `/github/installations` returns accounts plus installation IDs, while `/github/repositories` returns repositories with snake_case fields from GitHub (`id`, `full_name`, `default_branch`, `private`). This distinction matters when building TypeScript types.

**Security blocker for a browser picker:** Never put `GITHUB_INTEGRATION_ADMIN_TOKEN` in `NEXT_PUBLIC_*`, browser source, or a client-side fetch. The backend's temporary admin token is for trusted server-side administrative operations, not untrusted browsers. Before exposing management controls, implement an authenticated **server-side gateway** (e.g. protected Next.js server routes using Clerk user/organization permissions), or coordinate a proper app-user authorization mechanism in the FastAPI backend. Keep the GitHub App private key and webhook secret on Railway only. Avoid allowing every signed-in user to change the single-tenant SCADABLE repository.

**Suggested frontend flow:**

1. Show GitHub connection/sync status with the existing `/company` result, including pending/failed states.
2. Authorized admin installs GitHub App through GitHub's installation screen (the GitHub account owner chooses repositories there).
3. In an authenticated admin interface, select installation, then select an authorized repository using `/github/repositories` and `/github/selection` through the protected server gateway.
4. Display or configure the team-defined JSON file path (default `docs/scadable-features.json`). Ali explicitly delegated the **file and schema** design to the team; a browsable file picker is optional.
5. Invoke `/github/sync`, show `filePath`, `syncStatus`, `lastSyncedAt`, and errors from `/github/status`, then invalidate the React Query `company-profile` cache.
6. Keep manual sync separate from any “recompare now” action; label the latter as potentially billable.

**Acceptance tests for frontend:** no token leak in browser bundles or network traffic; authorized repo selection; clear empty/not-synced/pending/synced/failed UI; last good baseline preserved visually on a failed sync; no unnecessary competitor re-crawls.

## LLM teammate — contract and behavior

**No edits to `llm/` are required to receive the new baseline.** The backend's existing `backend/app/services/orchestrator.py` now supplies `get_scadable_baseline(db)` to both:

- `AnalyzerProtocol.analyze(scraped_data, baseline_context, trace_prefix=...)` for individual competitor research;
- `AnalyzerProtocol.compare(baseline_context, competitors, trace_prefix=...)` for overall SCADABLE-versus-competitor findings and feature comparisons.

The baseline dictionary maintains the existing Python contract:

```json
{
  "company_name": "SCADABLE",
  "website_url": "https://scadable.com",
  "domain": "Compliance automation",
  "summary": "Approved overview of the product",
  "supported_frameworks": ["SOC 2", "ISO 27001"],
  "core_capabilities": ["Automated evidence collection", "Audit readiness workflows"],
  "differentiators": ["Developer-friendly remediation guidance"]
}
```

The input feature **manifest** also requires `schema_version: 1`, but the validated baseline passed to the analyzer omits that version key. See `backend/examples/scadable-features.json` for a sample source file. The example is not confirmed SCADABLE product information; real content must be approved by the partner.

The backend does not manufacture LLM features, evidence, ratings, or citations. The LLM team should separately verify that its prompts/schema:

- Treat SCADABLE `core_capabilities` and `differentiators` as the current product baseline; do not silently use hardcoded older feature strings.
- Produce evidence-backed, explicitly paired competitor/SCADABLE feature scores, not false matches based on similar wording.
- Cite competitor facts using crawled source links, and distinguish GitHub-provided SCADABLE product claims from verified competitor evidence.
- Avoid generating a new comparison on **every** webhook or every file fetch; a successful baseline change only marks old overall SWOT as stale unless recompare is explicitly requested.
- Preserve compatibility with the current `AnalyzerProtocol` and backend response/DB contracts.

**Check to perform with LLM teammate:** run a real comparison once with baseline A, then replace the feature manifest with different approved baseline B, sync again, verify new analysis uses B while prior overall SWOT is marked stale. This must be verified against the configured provider; mocked pytest runs cannot prove prompt quality.

## Backend operator — setup, smoke test, and limitations

See `backend/docs/github-app-setup.md` for full setup and `curl` examples. Required secrets/vars on the backend service:

- `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_INTEGRATION_ADMIN_TOKEN`.
- Optional: `GITHUB_SYNC_ENABLED=true`, `GITHUB_SYNC_POLL_SECONDS=60`, `GITHUB_RECOMPARE_ON_SYNC=false` (safe defaults).

Grant the App **Contents: Read**, metadata and **Push** webhook subscription, and install on selected repositories. The webhook URL is `https://<backend-host>/github/webhook` (adjust for deployment route prefix only if your infrastructure adds one).

Install the feature manifest at `docs/scadable-features.json` in the selected repository or choose another `.json` path with `POST /github/file`. The root Dockerfile already runs `python -m app.db.init_db` on startup, so it creates the new `github_feature_state` table; this change does not modify existing table columns.

**Quick local test** (from repo root, after dependencies are installed):

```bash
cd backend
DATABASE_URL='sqlite:///:memory:' python -m pytest \
  tests/test_github_integration.py tests/test_github_db.py tests/test_github_feature_sync.py -q
```

**Full functional smoke test:** install App, verify `/github/installations`, choose repository and file, run manual `/github/sync`, confirm `/company` reflects approved capabilities, edit the selected manifest on the repository's default branch, confirm webhook delivery (HTTP 202), wait for worker, check `/github/status` becomes `synced`, and confirm the new capabilities appear. Test a malformed manifest and ensure the old good baseline stays in place. Run an explicit recompare only when LLM billing is approved.

**Scope/operational limitations:**

- This is a **single selected repository/file** for the current single-tenant SCADABLE backend, not per-user repository state.
- No live GitHub installation or Railway deployment has been exercised by these offline tests.
- The current `.github/workflows/ci.yml` is left unchanged as requested; its existing explicit backend test command does **not** include the new GitHub test modules. A maintainer must later add these tests to CI in a separate, authorized workflow edit, or run the manual command above.
- The worker only processes pushes while the backend runs; use an always-on instance. It fetches the configured file on every accepted default-branch push (even changes unrelated to the manifest), but does **not** trigger an LLM comparison unless explicitly enabled.
- The backend's existing authentication is not a complete user/organization permission system for browser-driven GitHub administration. Frontend management needs a secure gateway/auth design before going live.
- No automatic rewrite of historic competitor data or automatic new citations. Existing feature scores remain what prior analysis stored until an approved reanalysis is triggered.

## What teammates should and should not change

- **Backend owner:** validate live credentials / DB / webhook / versioned file, approve schema and permissions.
- **Frontend owner:** restore the currently commented GitHub panel and implement authenticated server-side management controls. Do not hardcode backend admin secrets into client JS.
- **LLM owner:** validate prompt/schema behavior using the dynamic baseline, paired evidence and citations; only change `llm/` on the LLM owner's branch if necessary.
- **Do not merge to `main` until** a real installation + sync + push + failure-retention smoke test succeeds and secrets are provisioned securely.
