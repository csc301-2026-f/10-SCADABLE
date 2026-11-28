# Live SWOT Developer and Maintenance Guide

This guide explains how to set up, test, deploy and maintain Live SWOT. For application access and user workflows, see [README.md](README.md).

[Local setup](#1-quick-local-setup) · [Configuration](#2-environment-configuration) · [Architecture](#3-architecture-and-project-layout) · [Testing](#4-automated-testing-and-quality-checks) · [Deployment](#5-deployment-and-handoff) · [Maintenance](#6-maintenance-and-troubleshooting)

## 1. Quick local setup

**Prerequisites:** Git, Node.js 22 with npm, Docker (running), and Python 3.12 or [uv](https://docs.astral.sh/uv/). Use macOS, Linux or WSL2. Default ports: 5432, 8000 and 3000.

```bash
git clone https://github.com/csc301-2026-f/10-SCADABLE.git
cd 10-SCADABLE
./setup.sh
```

The script prepares environment files, starts a loopback-only PostgreSQL 16 container, installs dependencies and Chromium, and initializes tables. Existing configuration and data are preserved.

- `./setup.sh --start`: install and start both servers.
- `./setup.sh --check`: check prerequisites.
- `./setup.sh --prepare`: create missing environment files only.
- `SCADABLE_DB_PORT=5436 ./setup.sh`: use an available database port on a fresh setup; reuse that port for later runs.
- `docker stop scadable-dev-postgres`: stop the database without deleting data.

Use manual setup for a custom database or a container owned by another checkout.

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

Open <http://localhost:3000>. The API health check at <http://localhost:8000/health> should return `{"status":"ok"}`. Swagger: <http://localhost:8000/docs>; schema: <http://localhost:8000/openapi.json>.

A fresh database is empty. Local UI testing is available without Clerk keys; local `next dev` enables the dashboard shortcut automatically. Real research through the dashboard requires Clerk Development credentials, an invited account and an OpenAI key. For a UI-only walkthrough, set `NEXT_PUBLIC_USE_MOCKS=true` in `frontend/.env.local` and restart Next.js. This uses fixtures, not live research. Never use mock crawling in production or production credentials locally.

### Manual setup / custom local database

```bash
./setup.sh --prepare
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt ./scraper -e ./llm -e '.[dev]'
mkdir -p scraper/.uv-cache/playwright
PLAYWRIGHT_BROWSERS_PATH="$PWD/scraper/.uv-cache/playwright" python -m playwright install chromium
```

If Python bootstrap fails, move the failed virtual environment aside and use `uv venv --python 3.12 backend/.venv`, then `uv pip install --python backend/.venv/bin/python -r backend/requirements.txt ./scraper -e ./llm -e '.[dev]'`. Linux may require `python -m playwright install --with-deps chromium` with the same browser path. Install the scraper non-editably and reinstall after source changes.

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

Clerk account keys are held in private metadata and forwarded for an authenticated research request only. The bridge token also authorizes cancellation. Hosted account access uses invite-only Clerk Development authentication. `ALLOW_DEV_DASHBOARD_BYPASS` is a server-side frontend setting for temporary evaluator access. In hosted environments with Clerk configured, the sign-in page shortcut, bypass route and bypass cookie acceptance respect the setting. Keep it false or unset outside evaluation. See Section 5 for the handoff procedure and security boundary. Keep separate keys and service variables in development and production. No live provider key is required for offline tests.

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
- `docs/` and `backend/docs/`: specialized integration guides; `deliverables/`: project milestone records.

A competitor research run queues, crawls pages, stores a snapshot and checks whether content or analysis policy changed. Unchanged content can reuse a previous analysis; changed content is analyzed and saved with citations. The orchestrator completes the individual run before refreshing the overall SWOT. Comparisons use SCADABLE's effective baseline and each changed competitor, then publish the combined findings under a revision guard. This prevents an older comparison replacing newer data. GitHub sync validates a structured manifest and stores the last good baseline. Background workers schedule research, process signed GitHub push events and recover delayed costs. PostgreSQL preserves results across deployments.

Why this design: separate crawler/analysis adapters permit offline fixtures and future provider changes; persistent snapshots reduce repeated model calls; incremental comparison limits cost and preserves unrelated findings. Frontend polls status separately from long-running requests. The API keeps user-facing types stable and repositories centralize storage operations.

See [research pipeline](docs/research-pipeline.md), [scraper architecture](scraper/docs/SCRAPER_ARCHITECTURE.md), [LLM package](llm/README.md) and [GitHub integration](backend/docs/github-app-setup.md).

## 4. Automated testing and quality checks

Tests use Python 3.12 and Node 22. Offline fixtures avoid external crawling and paid model calls; live pipeline and browser-loopback tests are opt-in.

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

The frontend suite includes 12 unit/contract tests covering validation, API client response handling (mocked HTTP transport), stale findings, error sanitization, polling and cost/cache calculations, plus 29 component tests using Vitest and React Testing Library. The component tests cover the add-competitor dialog, competitor list, agent runs, SWOT overview and research status banner. `npm test` runs all 41 tests; `npm run test:components` runs the component tests alone. These are not full browser end-to-end tests. To test the scraper independently, install [uv](https://docs.astral.sh/uv/) if needed, then run from the repository root:

```bash
cd scraper
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
```

### Continuous integration

GitHub Actions runs on pull requests targeting `main` and supports manual execution. The scraper and backend jobs have 10-minute timeouts; the frontend job has a 5-minute timeout. Run tests locally before opening a PR.

- [CI configuration](.github/workflows/ci.yml)
- [GitHub Actions run history](https://github.com/csc301-2026-f/10-SCADABLE/actions)

See the [CI results in the main README](README.md#testing).

| CI job | Checks |
| --- | --- |
| Scraper Checks | Ruff lint, formatting and scraper tests |
| Backend Tests | All backend tests (including crawler, scheduler, GitHub and PostgreSQL integration) and standalone LLM/pipeline tests, including setup tests |
| Frontend Checks | ESLint, unit/contract and component tests, and TypeScript |

CI uses an isolated PostgreSQL database and disables paid live research.

See the [main README](README.md#testing) for local regression and installation results. The local smoke test started the frontend and backend separately.

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

### Deployment architecture

The [course repository](https://github.com/csc301-2026-f/10-SCADABLE) is the source of reviewed code. Due to organization integration restrictions, Vercel and Railway deploy from the [deployment fork](https://github.com/Sukhmangill23/10-SCADABLE). Developers update `dev` for testing; GitHub Actions synchronizes course `main` into fork `main` for production. Pushes to the respective fork branches trigger automatic deployments. Each environment has its own API and PostgreSQL database.

```mermaid
flowchart LR
    PR[Feature branch and pull request] --> CI[CI checks and review]
    PR --> DEV[Fork dev branch]
    DEV --> DV[Vercel development]
    DEV --> DR[Railway development API]
    DV --> DR
    DR --> DDB[(Development PostgreSQL)]
    CI --> MAIN[Course main branch]
    MAIN --> SYNC[GitHub Actions sync]
    SYNC --> PROD[Fork main branch]
    PROD --> PV[Vercel production]
    PROD --> PRY[Railway production API]
    PV --> PRY
    PRY --> PDB[(Production PostgreSQL)]
```

| Environment | Branch | Frontend | Backend and data |
| --- | --- | --- | --- |
| Development | `dev` | [Development application](https://10-scadable-jm2j-git-dev-scadable1.vercel.app/competitors) | Separate Railway API and PostgreSQL database |
| Production | `main` | [Live SWOT](https://10-scadable-jm2j.vercel.app/) | Separate Railway API and PostgreSQL database |

### Configuring the hosting services

For both environments:

1. Connect Vercel and Railway to the deployment fork. Use `dev` for development (Vercel Preview) and `main` for production. Vercel uses root `frontend`, `npm ci` and `npm run build`. Railway uses the root `Dockerfile` and a separate PostgreSQL 16 service per environment. The Dockerfile installs packages and Chromium, initializes tables and starts Uvicorn on Railway's `PORT`.

2. Configure the backend and frontend variables in Section 2. Set `NEXT_PUBLIC_API_URL` and server-only `BACKEND_API_URL` to the matching environment's API URL; set CORS origins to the matching frontend URL. Use `NEXT_PUBLIC_USE_MOCKS=false` and `ALLOW_MOCK_CRAWLER=false` for real research. Never copy production database or provider secrets into development.

3. Develop on a feature branch and test in fork `dev`. Open a PR to course `main` with testing evidence and independent review. After merge, check the production sync action and deployed commits. Preserve fork-specific configuration; avoid force pushes.

4. Smoke-test `/health`, `/docs`, sign-in and an existing competitor or overview. With approval, run one paid research job in development; comparison and cost may arrive later. Check database isolation. For failed deployments, inspect logs and redeploy the last known good version after checking schema compatibility and backups.

Deployment screenshots are available in the [main README](README.md#deployment-and-cicd). For ongoing deployment status, see the [deployment fork Actions history](https://github.com/Sukhmangill23/10-SCADABLE/actions) and the Vercel and Railway project dashboards. Use the deployed commit and branch to identify which version is running in each environment.

### GitHub feature synchronization

Create/install a GitHub App with read-only Contents and push events, configure the signed webhook URL `<backend-url>/github/webhook`, and set the GitHub App credentials and admin token on the API and trusted frontend gateway. Select a repository and its product feature manifest using Our Company. Start with [example JSON](backend/examples/scadable-features.json), replace sample facts with partner-approved facts, and commit it in the selected repository. The integration tracks the selected repository's default branch; push a harmless approved manifest edit and verify pending → synced plus a new file revision. Do not manually sync when testing automatic sync. Confirm the overview becomes stale if the actual baseline changes; a successful new comparison updates it. `GITHUB_RECOMPARE_ON_SYNC=true` is optional and incurs model charges. See the [full setup guide](backend/docs/github-app-setup.md) for detailed configuration and failure recovery.

### Ownership and security

Maintenance requires access to GitHub (course repository, deployment fork and GitHub App), Vercel, Railway, Clerk, OpenAI and Langfuse. Handoff includes partner-owned service accounts, billing, database backups and secure credential transfer. Rotate secrets and revoke obsolete team access once partner access is established.

Clerk handles frontend authentication; server-side tokens protect sensitive integration and settings operations. Credentials remain in server environment variables or private account metadata.

#### Temporary evaluator access

The temporary read-only evaluator dashboard is intended for course grading. It allows visitors to view existing findings without a Clerk account. The frontend restricts mutation routes and controls to signed-in users, including competitor creation/deletion, research reruns/cancellation, GitHub changes and research settings. This is a dashboard-level restriction, not a complete backend authorization boundary. Before enabling evaluator access, verify the intended restrictions at the API level.

To configure evaluator access in Vercel:

1. Open the frontend project → Settings → Environment Variables. Set the server-side `ALLOW_DEV_DASHBOARD_BYPASS` to `true` for Production only when the evaluator access controls have been verified. Redeploy the production frontend.
2. Open the live site in a private browser window. Verify that existing findings are accessible without an account and that creating or deleting competitors, starting or cancelling research, and changing research or integration settings are denied, including through direct API requests.
3. After grading, set `ALLOW_DEV_DASHBOARD_BYPASS=false` in Production (and any other hosted environment where it was enabled), then redeploy each affected frontend. Verify that the shortcut is hidden, direct bypass requests fail, previously issued bypass cookies no longer grant access and invited users can still sign in.

Clerk invitations are managed through the Development instance under Users → Invitations. Do not publish evaluator or partner credentials in the public repository.

**Security considerations:** The evaluator toggle controls frontend dashboard access; it does not independently secure the backend. Direct FastAPI endpoints for competitor creation, deletion and reruns do not universally enforce Clerk authentication. Keep administrative access policies configured and restrict direct API access. Do not enable evaluator access on a live dataset unless server-side write restrictions have been verified. If verification fails, use an isolated evaluator environment with non-sensitive data and no paid provider credentials, or invited accounts. Disable the bypass for private partner operation. Rate limiting and crawler network restrictions remain further hardening priorities.

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
