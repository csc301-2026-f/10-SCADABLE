# Live SWOT — developer and maintenance guide

This guide is for developers maintaining the Team 10 CSC301 MVP after handoff. For product use and access, see [README.md](README.md).

## 1. Quick local setup

Use macOS or Linux (Windows: WSL2). Install Git, Node.js 22 with npm, Docker Desktop/Engine and Python 3.12 or [uv](https://docs.astral.sh/uv/). Start Docker and ensure ports 5432, 8000 and 3000 are available. Use a development checkout, not production credentials.

```bash
git clone https://github.com/csc301-2026-f/10-SCADABLE.git
cd 10-SCADABLE
./setup.sh
```

The script creates missing environment files without overwriting existing ones, starts a PostgreSQL 16 container bound to loopback, installs Python packages and Chromium, creates missing tables, and installs frontend dependencies. It prints startup commands. Use `./setup.sh --start` to run both local servers after setup, `./setup.sh --check` to check prerequisites, or `./setup.sh --prepare` to create only the missing environment files. Stop the local database with `docker stop scadable-dev-postgres`. Existing data is not deleted. The script intentionally refuses to use a custom database or a container belonging to a different checkout. If port 5432 is occupied, use an available port with `SCADABLE_DB_PORT=5436 ./setup.sh`. Use the same port for subsequent setup runs. Follow manual setup below if you want to use your own database.

Start the two processes in separate terminals if you did not use `--start`:

```bash
# Terminal A, repository root
cd backend
.venv/bin/python -m uvicorn app.main:app --reload --reload-dir app
```

```bash
# Terminal B, repository root
cd frontend
npm run dev
```

Visit http://localhost:3000; http://localhost:8000/health should return `{"status":"ok"}`. API documentation: http://localhost:8000/docs and http://localhost:8000/openapi.json. A clean local database starts empty. Without Clerk credentials, browsing the local dashboard is possible, but signed-in research gateways require configured development Clerk and an invited account. For offline UI-only development set `NEXT_PUBLIC_USE_MOCKS=true` explicitly and restart Next.js. This uses fixtures, not live research. For real research, configure development OpenAI credentials before starting a new run. Do not enable mock crawling in production.

### Manual setup / custom local database

```bash
./setup.sh --prepare
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt ./scraper -e ./llm -e '.[dev]'
mkdir -p scraper/.uv-cache/playwright
PLAYWRIGHT_BROWSERS_PATH="$PWD/scraper/.uv-cache/playwright" python -m playwright install chromium
```

If Homebrew Python fails during `ensurepip`, install uv and use `uv venv --python 3.12 backend/.venv` (after moving the failed virtual environment aside), then `uv pip install --python backend/.venv/bin/python -r backend/requirements.txt ./scraper -e ./llm -e '.[dev]'`. On Linux the browser may need system packages: use `python -m playwright install --with-deps chromium` with the same browser path. The scraper must be installed as a regular package (not editable), while LLM can be editable. Reinstall the scraper after changing its source.

Start a local PostgreSQL database, for example:

```bash
docker run --name scadable-local-custom -e POSTGRES_USER=scadable \
  -e POSTGRES_PASSWORD=scadable_local_only -e POSTGRES_DB=live_swot \
  -p 127.0.0.1:5436:5432 -d postgres:16
docker exec scadable-local-custom pg_isready -U scadable -d live_swot
```

Set `DATABASE_URL=postgresql+psycopg://scadable:scadable_local_only@127.0.0.1:5436/live_swot` in `backend/.env`, then run `(cd backend && .venv/bin/python -m app.db.init_db)` and `(cd frontend && npm ci)` from the root. Wait for PostgreSQL to be ready before initializing. Use an appropriate connection string for your own local database. The example password is local-only. Never use a Railway production database for development or tests.

## 2. Environment configuration

Copy the tracked examples (`./setup.sh --prepare`), edit locally, and never commit real keys. Process environment overrides dotenv values. Railway and Vercel use their own service variables, not local files.

| Location | Variables and purpose |
| --- | --- |
| Root `.env` | `OPENAI_API_KEY`, `OPENAI_MODEL` (structured-output-capable model), `OPENAI_TIMEOUT_SECONDS`; optional `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` for trace/cost reporting. |
| `backend/.env` | `DATABASE_URL`, `CORS_ORIGINS`, `ALLOW_MOCK_CRAWLER=false`. |
| Backend scheduler | `AUTO_REFRESH_ENABLED` (false locally; enable intentionally on an always-on service), `COMPETITOR_REFRESH_DAYS=7`, `COMPETITOR_REFRESH_RETRY_MINUTES=60`, `COMPETITOR_REFRESH_POLL_SECONDS=60`. Scheduled runs use deployment credentials. |
| Backend GitHub App | `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_INTEGRATION_ADMIN_TOKEN`, `GITHUB_SYNC_ENABLED`, `GITHUB_SYNC_POLL_SECONDS=60`, `GITHUB_RECOMPARE_ON_SYNC=false` (enable only if paid comparisons on sync are desired). |
| Backend cost recovery | `LANGFUSE_COST_RECOVERY_ENABLED`, `LANGFUSE_COST_RECOVERY_POLL_SECONDS=300`; first lookup is delayed by `LANGFUSE_COST_DELAY_SECONDS` (default 300 seconds). |
| Frontend `.env.local` | `NEXT_PUBLIC_API_URL=http://localhost:8000`, `BACKEND_API_URL=http://localhost:8000`, `NEXT_PUBLIC_USE_MOCKS=false`, development `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`. |
| Shared server-only settings | Same `SCRAPING_SETTINGS_ADMIN_TOKEN`, `GITHUB_INTEGRATION_ADMIN_TOKEN` and `ACCOUNT_RESEARCH_BRIDGE_TOKEN` on Railway and trusted Next.js server as needed. `SETTINGS_ADMIN_EMAILS` optionally limits settings administrators. Never use `NEXT_PUBLIC_` for these values. |

Clerk account keys are held in private metadata and forwarded for an authenticated research request only. The bridge token also authorizes cancellation. Do not enable `ALLOW_DEV_DASHBOARD_BYPASS` in partner production. Keep separate keys and service variables in development and production. No live provider key is required for offline tests.

## 3. Architecture and project layout

```mermaid
flowchart LR
    U[SCADABLE user] --> F[Next.js frontend / Clerk]
    F --> A[FastAPI routes]
    F --> G[Next.js authenticated server gateways]
    G --> A
    A --> O[Research orchestrator]
    O --> C[Crawler adapter / Crawl4AI]
    O --> L[Analysis adapter / OpenAI]
    O --> DB[(PostgreSQL)]
    A --> DB
    GH[GitHub push webhook] --> A
    DB --> S[Sync and refresh workers]
    S --> O
    L --> LF[Langfuse traces]
```

- `frontend/app/`: Next.js pages and server gateways; `components/`: dashboard UI; `lib/api/`: typed API clients; `lib/polling.ts`: bounded refresh logic.
- `backend/app/api/`: FastAPI routes, schemas, serializers and safe error handlers; `db/`: SQLAlchemy models and repositories; `services/`: research workflow, crawler/LLM adapters, scheduling, GitHub sync and costs.
- `scraper/`: independent website discovery, selection, extraction and export package; `llm/`: structured analysis, citations, scoring and comparison validation; `tests/`: standalone LLM and pipeline tests.
- `backend/tests/`, `scraper/tests/`, `frontend/tests/`: component tests; `.github/workflows/ci.yml`: automated checks.
- `docs/` and `backend/docs/`: specialized integration guides; `deliverables/`: academic history, not current setup instructions.

A competitor research run queues, crawls pages, stores a snapshot and checks whether content or analysis policy changed. Unchanged content can reuse a previous analysis; changed content is analyzed and saved with citations. The orchestrator completes the individual run before refreshing the overall SWOT. Comparisons use SCADABLE's effective baseline and each changed competitor, then publish the combined findings under a revision guard. This prevents an older comparison replacing newer data. GitHub sync validates a structured manifest and stores the last good baseline. Background workers schedule research, process signed GitHub push events and recover delayed costs. PostgreSQL preserves results across deployments.

Why this design: separate crawler/analysis adapters permit offline fixtures and future provider changes; persistent snapshots reduce repeated model calls; incremental comparison limits cost and preserves unrelated findings. Frontend polls status separately from long-running requests. The API keeps user-facing types stable and repositories centralize storage operations.

See [research pipeline](docs/research-pipeline.md), [scraper architecture](scraper/docs/SCRAPER_ARCHITECTURE.md), [LLM package](llm/README.md) and [GitHub integration](backend/docs/github-app-setup.md). Older planning notes may describe earlier versions; this guide is the current entry point.

## 4. Automated testing and quality checks

The CI workflow uses Python 3.12 and Node 22. It checks all backend test files (including crawler, scheduler, GitHub and PostgreSQL tests), standalone LLM/pipeline tests, scraper tests/lint/format, frontend ESLint, frontend unit tests and TypeScript. Test doubles avoid external crawling and paid model calls by default. CI's PostgreSQL service is dedicated to tests. Browser-loopback and live pipeline tests are opt-in.

Offline backend and standalone tests, from the repository root after setup:

```bash
export AUTO_REFRESH_ENABLED=false GITHUB_SYNC_ENABLED=false LANGFUSE_COST_RECOVERY_ENABLED=false
export RUN_LIVE_PIPELINE_TEST=0 SCRAPER_BROWSER_TESTS=0
unset TEST_DATABASE_URL
(cd backend && DATABASE_URL='sqlite:///:memory:' .venv/bin/python -m pytest tests/ -q)
backend/.venv/bin/python -m pytest tests/ -q
```

Run PostgreSQL integration tests against a disposable database ending `_test`, not the local app database. For the default setup container:

```bash
docker exec scadable-dev-postgres createdb -U scadable live_swot_test
cd backend
export TEST_DATABASE_URL='postgresql+psycopg://scadable:scadable_local_only@127.0.0.1:5432/live_swot_test'
DATABASE_URL="$TEST_DATABASE_URL" RUN_LIVE_PIPELINE_TEST=0 .venv/bin/python -m pytest tests/ -q
```

If the test database already exists, use a fresh disposable test DB or explicitly reuse it; never drop a database without verifying its name and contents. Use the actual host port if you configured a custom one. Unset `TEST_DATABASE_URL` when done. Tests that require PostgreSQL are skipped without it.

```bash
# Repository root, in a separate terminal
cd frontend
npm ci
npm run lint
npm test
npm run typecheck
```

The frontend tests exercise validation, actual API client response handling (mocked HTTP transport), stale findings, error sanitization, polling and cost/cache calculations. They are unit/contract tests, not a browser end-to-end suite. To test the scraper independently:

```bash
cd scraper
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
```

See [GitHub Actions runs](https://github.com/csc301-2026-f/10-SCADABLE/actions) for current results. The workflow has explicit timeouts (10 minutes for scraper/backend and 5 for frontend). Run locally before opening PRs, avoid unnecessary reruns and respect the course's Actions resource cap. The previous CI screenshot ([historical evidence](docs/screenshots/ci-pipeline.png)) is not evidence of a new run.

### API reference

FastAPI generates Swagger UI at `/docs` and the machine-readable schema at `/openapi.json`. `backend/tests/test_openapi.py` checks the paths, descriptions and response fields. IDs are strings in responses; API bodies use camelCase.

| Method / path | Purpose |
| --- | --- |
| `GET /health`, `GET /company` | Liveness and current SCADABLE baseline |
| `GET /competitors`, `POST /competitors` | List / create and queue research |
| `GET /competitors/{competitor_id}`, `DELETE /competitors/{competitor_id}` | Profile / delete |
| `POST /competitors/{competitor_id}/rerun`, `GET /competitors/{competitor_id}/changes` | Refresh / compare completed runs |
| `GET /runs`, `GET /runs/{run_id}`, `POST /runs/{run_id}/cancel` | History, status, cancellation (bridge token) |
| `GET /swot/overview`, `GET /swot` | Overall / requested SWOT |
| `GET /settings/scraping`, `PUT /settings/scraping` | Shared page limit (write requires admin token) |
| `/github/installations`, `/github/repositories`, `/github/status`, `/github/selection`, `/github/file`, `/github/sync`, `/github/features` | Protected GitHub App management; methods in Swagger |
| `POST /github/webhook` | Signed push events |

Do not use production Swagger to create/delete competitors casually; research can incur cost. Common errors: 401 unauthorized, 404 missing record, 409 duplicate or active run, 422 invalid data, 503 missing service configuration. Never share request headers containing secrets.

## 5. Deployment and handoff

The course repository is the source of reviewed code. The team deployment fork is connected to Vercel and Railway because the course organization restricts third-party integrations. The team has reported separate `dev` and `main` (production) deployments and a production sync workflow on the fork. The fork's Actions workflow and hosting configuration are external to this archive; the repository CI workflow does not itself deploy services. Confirm the actual fork URL, workflow, branch settings and service addresses in the dashboards at handoff rather than assuming they exist in a new fork.

| Environment | Branch | Frontend | Backend and data |
| --- | --- | --- | --- |
| Development | Deployment fork `dev` | Vercel development project/environment | Separate Railway API and PostgreSQL database |
| Production | Deployment fork `main` | [Live SWOT](https://10-scadable.vercel.app/) (existing project URL; confirm) | Separate Railway production API and PostgreSQL database |

For both environments:

1. Connect the matching branch to its Vercel project (root `frontend`, install `npm ci`, build `npm run build`) and Railway API service (repository root as build context, root `Dockerfile`). Use a separate PostgreSQL 16 service. The Dockerfile installs sibling packages and Chromium, initializes missing tables, then starts Uvicorn on Railway's `PORT`.
2. Configure the backend and frontend variables in Section 2. Set `NEXT_PUBLIC_API_URL` and server-only `BACKEND_API_URL` to the matching environment's API URL; set CORS origins to the matching frontend URL. Use `NEXT_PUBLIC_USE_MOCKS=false` and `ALLOW_MOCK_CRAWLER=false` for real research. Never copy production database or provider secrets into development.
3. Develop on a feature branch; open a PR against course `main` with purpose, testing evidence and independent reviewer. Test changes in the deployment fork's `dev` branch before production. After review/merge, verify the fork's existing production sync action brought over the intended commit. Vercel/Railway should redeploy automatically on their configured branch changes. Inspect deploy commit and logs for each service. Avoid force pushing the fork or overwriting fork-specific configuration.
4. Check `/health`, `/docs`, sign-in and an existing competitor/overview in the deployed app. For an approved paid test, run one research job and inspect the saved results; comparison and cost can arrive later. Confirm development changes did not modify production data. If a deploy fails, check build/runtime logs, restore the last known good code or redeploy the prior version. Check schema compatibility and database backups before rollback.

Earlier deployment evidence: [Railway](docs/screenshots/railway-auto-deploy.png), [Vercel](docs/screenshots/vercel-auto-deploy.png). These screenshots document previous deployments, not current settings for both environments. The hosting owner must verify the current dev/prod automatic deployment state and capture current evidence for D4.

### GitHub feature synchronization

Create/install a GitHub App with read-only Contents and push events, configure the signed webhook URL `<backend-url>/github/webhook`, and set the GitHub App credentials and admin token on the API and trusted frontend gateway. Select a repository and its product feature manifest using Our Company. Start with [example JSON](backend/examples/scadable-features.json), replace sample facts with partner-approved facts, and commit it in the selected repository. The integration tracks the selected repository's default branch; push a harmless approved manifest edit and verify pending → synced plus a new file revision. Do not manually sync when testing automatic sync. Confirm the overview becomes stale if the actual baseline changes; a successful new comparison updates it. `GITHUB_RECOMPARE_ON_SYNC=true` is optional and incurs model charges. See the [full setup guide](backend/docs/github-app-setup.md) for detailed configuration and failure recovery.

### Ownership and security at handoff

Transfer or invite partner maintainers to the GitHub repository/deployment fork, Vercel, Railway, Clerk, GitHub App and OpenAI/Langfuse accounts. Ensure partner-owned billing, backup access and secret rotation; transfer secrets through a secure channel, never a repo or chat log. Revoke obsolete team access after confirming the partner can deploy and sign in. Record environment URLs, service owners and deployment branches in the partner's internal handoff record. Do not publish passwords or invite links.

**Known deployment security boundary:** Clerk protects the frontend and signed-in server gateways, and admin/bridge tokens protect settings writes, GitHub management and cancellation. The current FastAPI competitor create/rerun/delete and read routes do not universally enforce Clerk identity. CORS is not authentication. Before public partner deployment, restrict direct API access with an authenticated gateway/private network and implement/test authorization for all data and billable mutations; migrate browser calls through that gateway. This is an existing architecture limitation, not a claim that all APIs are secured. Review scraping URL/network restrictions and rate limits as part of that hardening. No broad authentication migration is included in this D4 cleanup because it would change deployed contracts.

## 6. Maintenance and troubleshooting

- **Dependencies:** use lockfile for frontend/scraper; pin/test upgraded backend dependencies. Reinstall scraper after changing it. Do not update model prompts or analysis policy without testing citations and scores; bump `ANALYSIS_POLICY_VERSION` or comparison policy version when appropriate.
- **Database:** use Railway backups and test restore on a separate database. `create_all` only creates missing tables; it is not a schema migration system. Plan and review explicit migrations for changed columns, preserve data and back up before release. Do not drop/recreate production tables.
- **Research fails:** check website accessibility and backend logs, configured OpenAI model/key, crawler Chromium and page budget. A failed real crawl should not fall back to fake data. A failed run leaves previous findings. Do not repeatedly rerun every competitor; check error details and fix the cause.
- **Scores missing / overview stale:** individual success precedes the comparison. A comparison can fail validation or be superseded by a newer revision. Check logs; after active runs settle, rerun one competitor if needed. Previous scores may be displayed while a new comparison is pending. Do not bypass validation to force results.
- **Delayed costs / cache:** Langfuse may take several minutes and the backend retries/recovery worker may fill missing costs later. Verify tracing keys and cost worker logs; a dash means not available. Cache rate measures reuse of analysis based on content, not the provider prompt cache. A true 0% is possible when research changes.
- **Automatic refresh:** ensure `AUTO_REFRESH_ENABLED=true` and an always-on API service. A successful run schedules the next attempt; failures use retry backoff. Pending/running jobs suppress duplicate scheduling. Review logs if a worker restarts; abandoned jobs are recovered after the stale window. This can spend API credits even without a user signed in.
- **GitHub sync pending/failed:** check GitHub App installation, default branch/file path, webhook delivery/signature, worker credentials and logs. Correct manifest errors; preserve the last valid baseline. Verify status and revision rather than just pressing Sync.
- **Frontend cannot connect:** verify `/health`, both API URLs, CORS, Clerk keys and server-only shared tokens. Restart/redeploy after env changes; browser variables are set at build time. Use separate dev/prod settings.
- **Setup errors:** start Docker; check ports; use uv if Python bootstrap fails; verify PostgreSQL readiness; install Chromium dependencies on Linux. Existing files/container are retained; use manual setup if the default container belongs to another checkout.

## 7. Release checklist

Before tagging `D4`: get independent PR reviews, complete all CI checks and a fresh-clone setup on a supported machine; verify both deployments, live conflict data when present, scheduled refresh, automatic GitHub sync and stale recovery; rerun Lighthouse/keyboard checks; confirm partner access and maintenance documentation; create a GitHub release tagged `D4` from the reviewed course main commit and submit the release link on Quercus. Do not present a test fixture or historical screenshot as proof of a current live run.
