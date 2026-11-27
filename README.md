# Live SWOT

**Team 10 (Niche) | CSC301 | Deliverable 4**

Live SWOT helps SCADABLE track competitors and understand how its product compares with theirs. SCADABLE develops software that helps companies meet security and regulatory requirements. Its team previously had to collect competitor information manually. Live SWOT brings this research together in one dashboard.

SWOT stands for **strengths, weaknesses, opportunities and threats**. The overall SWOT shows SCADABLE's relative strengths and weaknesses, potential areas for growth and external competitive threats. Findings link to their supporting sources where available.

[Live application](https://10-scadable.vercel.app/) · [Quick local setup](#quick-local-setup) · [User workflows](#main-workflows) · [Testing](#testing) · [Developer guide](DEVELOPERS.md)

## Access the application

- [Open Live SWOT](https://10-scadable.vercel.app/)
- [Developer setup and maintenance guide](DEVELOPERS.md)
- [Auth / invitations](frontend/docs/auth-access.md)

Sign in with your invited SCADABLE account. Access is **invite-only** via Clerk’s **Development** instance (`pk_test_` / `sk_test_`), not Production — that avoids needing a custom domain on our free Vercel URL. If you do not have access, ask an administrator to invite you from the Clerk Dashboard (**Users → Invitations**, Development instance). For a quick evaluation, open an existing researched competitor and the overall SWOT before starting a new research run. If you cannot access the hosted application, follow the local setup below.

## Quick local setup

For TAs and new contributors who want to run the application locally.

**Prerequisites:** Git, Node.js 22 with npm, Docker (running), and Python 3.12 or [uv](https://docs.astral.sh/uv/). Use macOS or Linux (Windows: WSL2). Ensure ports 3000, 8000 and 5432 are available.

```bash
git clone https://github.com/csc301-2026-f/10-SCADABLE.git
cd 10-SCADABLE
./setup.sh --start
```

The script creates missing environment files, starts a local PostgreSQL database, installs dependencies and Chromium, initializes database tables, and starts both application servers. Existing environment files are preserved. The first installation requires internet access for dependencies and browser downloads.

- Dashboard: <http://localhost:3000>
- Backend health check: <http://localhost:8000/health>
- Interactive API documentation: <http://localhost:8000/docs>

If port 5432 is already occupied, use a free database port on a fresh setup:

```bash
SCADABLE_DB_PORT=5436 ./setup.sh --start
```

Use the same database port on subsequent runs. Press Ctrl+C to stop the application servers; `docker stop scadable-dev-postgres` stops the local database without deleting its data.

**Evaluating locally:** A fresh database starts empty. Creating or rerunning real research requires **Clerk Development** keys, an invited account and an OpenAI key. See [environment configuration](DEVELOPERS.md#2-environment-configuration) and [auth access](frontend/docs/auth-access.md). For a UI-only walkthrough without paid research, set `NEXT_PUBLIC_USE_MOCKS=true` in `frontend/.env.local` and restart the frontend. This displays sample data, not live research. Do not use Clerk Production keys or the production database locally. The old **Go to dashboard** bypass button was removed; do not set `ALLOW_DEV_DASHBOARD_BYPASS` on Vercel.

For manual installation, port conflicts, separate server commands and troubleshooting, see the [developer guide](DEVELOPERS.md#1-quick-local-setup).

## Main workflows

### 1. Track a competitor

Open **Competitors** and select **Add competitor**. Enter a company name and its public website. Adding the company starts a background research run. You can return to the list, search for the company or open its profile while research is running. Duplicate websites are rejected. Removing a competitor removes its saved research and triggers an overall SWOT refresh; only remove companies you no longer need.

![Competitors dashboard showing tracked companies and research status](docs/screenshots/d4-competitors.png)

### 2. View competitor research

Open a competitor profile after a successful run to view its summary, cited findings, recent activity, open roles when available, and feature scores. The comparison chart shows only features for which both SCADABLE and the competitor have a score; other competitor scores appear in the feature list. Missing information is not a zero score. The **Conflicting data** section displays contradictory claims with their sources when identified. An empty section means no conflicts were identified in the available research, not that every claim has been independently verified.

Use **Rerun agent** to collect updated information, or **Cancel** while a run is active if cancellation is configured. **Since last run** compares completed research runs. The first run has no previous result to compare against. When automatic refresh is enabled, **Next refresh** shows when the next attempt is due. The default interval is seven days, with a retry after a failed attempt. Previous successful findings remain available if a new run fails or is cancelled.

![Competitor profile showing feature scores and comparison charts](docs/screenshots/d4-competitor-profile.png)

![Research changes and next scheduled refresh](docs/screenshots/d4-research-refresh.png)

### 3. Review SCADABLE's overall SWOT

Open **Overview** to view the combined analysis of researched competitors. The system compares each competitor against SCADABLE's product information and combines the results. When one competitor changes, its comparison is updated; unrelated comparisons can be reused. The overview retains the last successfully published findings if new data makes them outdated or a comparison fails. A warning indicates that the results are stale until a new comparison succeeds.

![Overall SWOT dashboard showing strengths, weaknesses, opportunities and threats](docs/screenshots/d4-swot-overview.png)

### 4. Keep SCADABLE's product information current

Open **Our Company** to see SCADABLE's offerings and positioning. An authorized user can connect a repository that the GitHub App has permission to access and select a JSON product feature file. This file describes SCADABLE's current product capabilities; it is not a scan of arbitrary source code. The system validates the file and syncs changes from the tracked default branch automatically when configured. The previous valid product profile stays available if a sync fails. Changes to product information mark the overall SWOT stale; a successful comparison updates it. See the [GitHub setup guide](backend/docs/github-app-setup.md) for administrator instructions.

![Our Company page showing the connected GitHub feature file and sync status](docs/screenshots/d4-github-sync.png)

### 5. Monitor research and spending

Open **Agent Runs** to review the latest finished run for each competitor, its status, processing steps and available cost. Weekly figures summarize returned run history. A successful run may appear before feature comparisons and cost estimates are ready. A dash indicates unavailable cost or cache information, rather than a measured zero. Recorded cost includes available estimates, not necessarily the full provider invoice. The cache hit rate measures reuse of existing analysis when scraped content has not changed; it is not the OpenAI provider's prompt-cache rate. Costs and scores may take several minutes to appear.

![Agent Runs dashboard showing research status and recorded costs](docs/screenshots/d4-agent-runs.png)

### 6. Adjust research settings

Open **Research settings** from the account menu. A signed-in user can configure an OpenAI API key or use the backend's configured default. Scheduled refreshes use the backend key. Authorized users can set the shared scraping depth to low (5 pages), medium (10 pages) or high (15 pages) to balance research detail, time and cost. The limit applies to future crawls, not those already running.

![Research settings showing API key options and scraping depth](docs/screenshots/d4-research-settings.png)

## What to expect

Research relies on publicly available websites and AI-generated analysis. A site may block automated crawling, have missing information or return inconsistent claims. Failed crawls do not publish fabricated findings in the real deployment. Scores are comparative estimates, not independent product audits. Review source links and use human judgment when making decisions. Research and automatic refresh may incur external API charges. For a quick evaluation, browse an existing completed competitor first.

## Testing

The project includes automated tests for the backend, website crawler, AI analysis pipeline and frontend. GitHub Actions runs tests and code quality checks on pull requests to `main`.

After local setup, run these commands from the repository root. They use offline test data and disable scheduled research, GitHub sync and cost recovery.

```bash
export AUTO_REFRESH_ENABLED=false GITHUB_SYNC_ENABLED=false LANGFUSE_COST_RECOVERY_ENABLED=false
export RUN_LIVE_PIPELINE_TEST=0 SCRAPER_BROWSER_TESTS=0
unset TEST_DATABASE_URL
(cd backend && DATABASE_URL='sqlite:///:memory:' .venv/bin/python -m pytest tests/ -q)
backend/.venv/bin/python -m pytest tests/ -q
(cd frontend && npm run lint && npm test && npm run typecheck)
```

D4 local verification (development machine):

| Check | Result |
| --- | --- |
| Backend (SQLite) | 229 passed, 2 skipped |
| Backend (PostgreSQL) | 267 passed, 1 skipped |
| Scraper | 513 passed, 10 skipped |
| Standalone LLM/pipeline checks | 3 passed, 3 skipped |
| Frontend | 12 passed |
| Setup script | 2 passed |
| Code quality | Ruff, ESLint and TypeScript checks passed |
| Frontend dependency audit | No reported vulnerabilities at time of testing |
| Local installation | Clean checkout-equivalent setup, database initialization and core application smoke checks passed |

These are local results, not a claim that the final release CI has passed. Backend suites overlap; their counts are reported separately. See [current CI runs](https://github.com/csc301-2026-f/10-SCADABLE/actions) and the [full testing guide](DEVELOPERS.md#4-automated-testing-and-quality-checks) for PostgreSQL, scraper and optional integration test commands.

## Non-Functional Requirements

The project aims to be accessible, secure, reliable and maintainable. The table identifies implemented safeguards and evidence; it does not imply a complete security or accessibility audit.

| Requirement | Implementation and evidence |
| --- | --- |
| Accessibility | Keyboard-operable lists, Escape/focus handling in dialogs, arrow-key chart tabs, labeled forms, error announcements and an accessible matched-score table. Earlier Lighthouse accessibility results: [98](docs/screenshots/lighthouse-accessibility-98.png) and [96](docs/screenshots/lighthouse-accessibility-96.png). Recheck current pages before handoff. |
| Input validation | Frontend and backend validate required fields, URL schemes, length and duplicates. See `frontend/lib/validation/competitor.ts`, `frontend/tests/workflows.test.mjs` and `backend/tests/test_api.py`. |
| Secret handling | Clerk private metadata and server environment store private credentials; protected GitHub/scraping operations and cancellation use server-side tokens. Signed GitHub events are verified. See `test_github_integration.py`, `test_account_research.py` and `test_scraping_settings.py` under `backend/tests/`. Direct backend access still requires deployment-level hardening; see the developer guide. |
| Error handling | Failed and cancelled research preserve earlier successful findings. Server errors are sanitized. See `backend/tests/test_cancel_run.py`, `test_api.py`, `test_overall_swot_review.py` and frontend workflow tests. |
| Data integrity | Unchanged website content can reuse analysis; invalid or superseded comparisons do not overwrite published results. Missing score values remain unavailable. See `backend/tests/test_incremental_overall_swot.py`, `test_llm_adapter.py` and `test_overall_swot_review.py`. |
| Maintainability | Separate crawler, analysis, persistence and orchestration layers; documented API; automated backend, scraper, LLM and frontend checks. See [developer guide](DEVELOPERS.md) and [CI configuration](.github/workflows/ci.yml). |

## Development and handoff

For local installation, architecture, environment variables, tests, deployments and troubleshooting, see [DEVELOPERS.md](DEVELOPERS.md). The partner should receive access to the repository, deployment services, database backups and service accounts through their owners, not credentials in this README.

## License

Live SWOT was developed for CSC301 in collaboration with SCADABLE. The partner has permitted sharing the work for academic collaboration; formal licensing and future distribution should be agreed with SCADABLE.
