# Live SWOT

**Team 10 – Niche | CSC301 | Deliverable 3**

Live SWOT is a competitor-intelligence application developed for SCADABLE. It automatically researches competing companies and organizes findings into a SWOT analysis, helping SCADABLE understand its competitive position without manually researching every competitor.

## Partner Introduction

Our project partner is **SCADABLE**, an AI-powered compliance company that helps startups and small and medium-sized businesses automate security compliance processes, including SOC 2 and HIPAA preparation.

Our primary contact is **Ali Rahbar**, co-founder of SCADABLE (a.rahbar@scadable.com). **Hossein Molavi**, also a co-founder (h.molavi@scadable.com), has been involved in project communications.

## Project Description

### The Problem

SCADABLE operates in a competitive market where companies regularly introduce new products, features, integrations, and compliance capabilities.

Tracking this information manually is time-consuming. It is difficult for SCADABLE's founders and sales team to keep up with competing offerings and understand how their own product compares.

### Our Solution

**Live SWOT** centralizes competitor research in one application.

Users can add companies to track, initiate automated website research, view individual competitor profiles, and examine an overall SWOT analysis from SCADABLE's perspective.

The application helps users identify SCADABLE's strengths, weaknesses, market opportunities, and competitive threats using information collected from competitor websites.

## Key Features and Use Cases

### 1. Authentication and Dashboard Access

Live SWOT includes a sign-in page implemented using **Clerk**. Account registration is currently invitation-only for SCADABLE and the course team.

For D3 evaluation, the hosted application provides a **Dev only → Go to dashboard** shortcut at the **top right** of the sign-in page. TAs can use it to enter the dashboard without a Clerk account or invitation. The same shortcut is available during local development.

**Example use case:** A course evaluator opens the [D3 application](https://10-scadable.vercel.app/competitors), selects **Go to dashboard** at the top right if prompted to sign in, and explores the competitor research dashboard.

![Live SWOT sign-in page](docs/screenshots/image-1.png)

### 2. Competitor Management

Users can view tracked competitors, search and filter the list, and add new companies by entering their names and website URLs.

**Example use case:** A SCADABLE team member wants to expand their competitive research by tracking Vanta alongside Drata. They use the **Add Competitor** form to enter Vanta's name and website URL, allowing Live SWOT to automatically research the company and generate competitive insights.

![Tracked competitors list](docs/screenshots/image2.png)

![Add competitor form](docs/screenshots/image3.png)

### 3. Automated Competitor Research

Live SWOT collects content from competitor websites using Crawl4AI and processes it through an OpenAI-powered **two-stage research pipeline**:

1. **Extract information:** Gather structured, source-linked facts, product capabilities, supporting citations, and conflicting evidence from crawled pages.
2. **Compare and score:** Compare the extracted competitor information with SCADABLE's baseline to produce the overall SWOT, competitor-specific SWOT findings, and feature scores for both sides.

Research results are saved in PostgreSQL, allowing users to revisit completed findings without manually repeating the entire process. The scraper also tracks research decisions and supports cost instrumentation.

After a successful research and comparison run, the SWOT overview and score visualizations update with the newly saved results. During D3 deployment testing, the research-to-results workflow completed successfully and the SWOT and feature scores appeared after the run finished.

**Example use case:** After adding Vanta as a competitor, a SCADABLE team member opens Agent Runs to monitor the crawl, extraction, comparison, and saved results, then reviews the competitor's cited findings and scores.

![Vanta automated competitor research results](docs/screenshots/image4.png)

### 4. Competitor Profiles

Each tracked company has a profile showing its website, company summary, research status, recent activity, citations, and researched findings.

The **Feature & Automation Score** and **Scores vs SCADABLE** charts are connected to the backend research results. The updated comparison pipeline generates competitor and SCADABLE feature ratings, pairs comparable capabilities, and displays the scores after a successful research/comparison run. During D3 deployment testing, the comparison scores populated consistently. Unavailable values remain missing rather than being shown as an invented 0/10.

The **Activity over time** chart displays past research activity.

**Not completed for D3:** The **Open Roles** field is visible and connected to an API field, but automated extraction and reliable population of job-opening counts are not complete. The **Next Refresh** field has API/UI support and scheduling-related backend logic, but automatic scheduled refreshes are not operational or verified in the deployed D3 system. It may display **Not scheduled**; this is not a promise of an upcoming refresh.

**Example use case:** A sales team member opens Vanta's profile to inspect researched capabilities and citations, compare feature scores with SCADABLE, and review research history.

![Vanta competitor profile and research activity](docs/screenshots/image5.png)

### 5. Overall SWOT Analysis

The SWOT Overview presents SCADABLE's competitive position in four categories:

- **Strengths:** SCADABLE's competitive advantages and capabilities.
- **Weaknesses:** Supported limitations or disadvantages.
- **Opportunities:** Areas for growth or improved competitive positioning.
- **Threats:** Market and competitor-related challenges.

The two-stage comparison uses SCADABLE's current **static product baseline** alongside extracted competitor evidence to produce an overall SWOT and cited competitor-specific findings. Live GitHub synchronization is not part of the D3 evaluation flow.

The previous issue in which the overview frequently stayed stale after a completed research run has been addressed in the updated integration. **During D3 deployment testing, a successful research/comparison run populated the SWOT Overview reliably once processing finished.** The interface still distinguishes running, failed, and unavailable analysis; success is not guaranteed if a website blocks crawling or an external service fails.

**Example use case:** A SCADABLE founder completes research for Vanta or Drata, opens SWOT Overview, and reviews the updated four-quadrant findings with supporting evidence.

![Live SWOT Overview showing competitive analysis](docs/screenshots/image-7.png)

### 6. Agent Runs, Research History, and API Costs

The **Agent Runs** page lets users monitor automated competitor research with run status, workflow steps, cached results, and estimated API costs.

A research run includes website crawling, information extraction, AI comparison/scoring, and persistence of the results. The cost dashboard is connected to the backend's cost-tracking data and displays recorded usage costs for completed research runs. The updated integration supports LLM usage accounting and scraper Decisions API tracking. **The Agent Runs cost dashboard was tested on the deployed D3 application and displayed costs successfully.**

Estimated costs reflect the usage successfully recorded at the time of viewing. A missing cost is displayed as unavailable rather than implying the API call was free; external cost reporting can be delayed or incomplete.

**Example use case:** A team member starts competitor research, checks Agent Runs for successful completion, verifies the estimated API cost, and then opens the updated competitor profile and SWOT Overview.

![Completed research run with workflow steps](docs/screenshots/image6.png)

## How to Use Live SWOT

### Deployed Application and TA Access

**D3 application:** https://10-scadable.vercel.app/

**Competitors page:** https://10-scadable.vercel.app/competitors

**Backend API:** https://backend-d3-production.up.railway.app/

**TA access (no account required):** Open the D3 application. On the sign-in page, click **Go to dashboard** in the **top-right corner** (next to **Dev only**). The dashboard opens without a Clerk invitation, just as in D2. Invited users can alternatively sign in through Clerk. For a locally run development instance, the same **Go to dashboard** shortcut is available without Clerk keys.

### Suggested Evaluation Workflow

1. Open https://10-scadable.vercel.app/competitors (or http://localhost:3000 for local setup).
2. If redirected to sign-in, click **Dev only → Go to dashboard** at the top right; no account is required for course evaluation.
3. Browse **Competitors** and open an existing researched company.
4. To test a new run, use **Add Competitor** with a unique company name and a reachable homepage URL.
5. Open **Agent Runs** to follow the research progress and inspect the recorded API cost once available. Some websites block automated crawling; those runs may fail rather than return fabricated results.
6. When the run succeeds and its comparison finishes, return to the competitor profile to inspect its summary, cited findings, and **Feature & Automation Score**.
7. Open **Scores vs SCADABLE** to review the paired competitor and SCADABLE ratings generated by the comparison stage.
8. Visit **SWOT Overview** to see the refreshed overall SWOT. In D3 deployment testing, the SWOT and feature scores were available after completed successful runs.

**Known incomplete fields:** **Open Roles** is not yet reliably populated through automated extraction, and **Next Refresh** does not represent an operational automatic refresh schedule in D3.

Live research incurs external API usage and may take time. For a quick evaluation, use an existing competitor with a completed run.

### D3 Improvements Since D2

D3 delivers substantial improvements to the core research experience:

- **Reliable research results:** Crawl failures are reported rather than silently replaced by mock data in normal deployment. The updated backend connects the new two-stage LLM extraction/comparison workflow to persistent competitor research and the overall SWOT.
- **SWOT after research:** The comparison stage generates SCADABLE-focused SWOT findings and cited competitor findings. During deployed D3 testing, the SWOT updated reliably after successful research and comparison.
- **Feature scores and SCADABLE comparisons:** Competitor and SCADABLE feature scores are generated in the comparison stage and shown in the profile charts after completed runs, with explicit unavailable-data handling.
- **API cost reporting:** The Agent Runs dashboard displays estimated recorded costs. Backend integrations add research-run cost/tracing support for LLM requests and scraper Decisions API usage, with handling for incomplete usage data.
- **Scraper improvements:** Updated discovery, content cleaning, and decisions tracing support the research pipeline; changes to processing rules can trigger reanalysis when necessary.
- **CI/CD:** GitHub Actions now has **three checks**—backend tests, frontend linting, and **LLM/pipeline tests**—alongside automatic deployments through Railway and Vercel.
- **Refresh and profile infrastructure:** The API/UI includes Open Roles and Next Refresh fields, and the backend includes scheduling logic (PRs [#62](https://github.com/csc301-2026-f/10-SCADABLE/pull/62), [#63](https://github.com/csc301-2026-f/10-SCADABLE/pull/63), [#64](https://github.com/csc301-2026-f/10-SCADABLE/pull/64)). These features remain incomplete for D3 as described below.

**Current D3 limitations:** Automatic scheduled research refresh is **not fully operational**; `AUTO_REFRESH_ENABLED=false` remains the intended D3 setting and Next Refresh may show **Not scheduled**. Automatic job-opening extraction for **Open Roles** is also incomplete. Some sites restrict crawling, and occasional external LLM, scraper, or cost-reporting failures remain possible. Research costs are estimates based on successfully recorded usage, not guaranteed invoices. SCADABLE's product baseline is static rather than synced live from GitHub.

## Technical Overview

Live SWOT uses the following technologies:

| Component | Technology | Purpose |
|---|---|---|
| Frontend | Next.js, React, TypeScript | Web interface |
| Authentication | Clerk | Sign-in and account management |
| Backend | Python, FastAPI | API and research orchestration |
| Database | PostgreSQL | Persistent application data |
| Web research | Crawl4AI | Competitor website crawling |
| AI analysis | OpenAI | Structured competitive analysis |
| Frontend deployment | Vercel | Hosted web application |
| Backend and database deployment | Railway | Hosted API and PostgreSQL |

The current research workflow follows these main stages:

```text
Add competitor
      ↓
Queue research run
      ↓
Crawl company website with Crawl4AI
      ↓
Extract structured, cited facts with OpenAI
      ↓
Compare with SCADABLE, generate SWOT and feature scores
      ↓
Save findings, scores, run status, and available costs to PostgreSQL
      ↓
Display competitor profiles, Agent Runs, and SWOT Overview
```

## Local Development and TA Onboarding

These are the steps for a clean checkout. **Prerequisites:** Git, **Python 3.12**, **Node.js 22** with npm, and **Docker Desktop** (for the easiest local PostgreSQL setup) or a separately installed PostgreSQL server. Real web crawling also needs Playwright Chromium. Commands below target macOS/Linux; Windows users should adapt virtual-environment activation and shell syntax.

### 1. Clone and configure all three environment templates

From a directory where you want the new checkout, run:

```bash
git clone https://github.com/csc301-2026-f/10-SCADABLE.git
cd 10-SCADABLE
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

The commands below assume you are at the **repository root** (the folder containing `frontend/`, `backend/`, `scraper/` and `llm/`), unless the section explicitly changes directories.

Edit the three newly created files **locally**. The templates contain placeholders, not working credentials. Do not commit `.env`, `backend/.env`, `frontend/.env.local`, API keys, database passwords or tokens.

| File | Variables | When needed |
| --- | --- | --- |
| Root `.env` (LLM + scraper) | `OPENAI_API_KEY` | Required for real OpenAI analysis and LLM-guided scraper selection; obtain a development key from the team. Never commit it. |
| Root `.env` | `OPENAI_MODEL` | Required for real analysis. Choose a model supported by your account that accepts structured outputs through the Responses API. |
| Root `.env` | `OPENAI_TIMEOUT_SECONDS` | Optional analysis timeout; template defaults to `30`. |
| Root `.env` | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | Optional cost tracing; both keys are required to enable Langfuse tracing. |
| `backend/.env` | `DATABASE_URL` | Required PostgreSQL connection string; change the example username/password/database to match your local database. |
| `backend/.env` | `CORS_ORIGINS` | Comma-separated allowed frontend origins; for local use include `http://localhost:3000`. |
| `backend/.env` | `ALLOW_MOCK_CRAWLER` | Keep `false` for real research; `true` is **offline development only** and may substitute mock pages on crawl failure. |
| `backend/.env` | `AUTO_REFRESH_ENABLED` | Keep `false` for D3: automatic refresh execution is not fully verified. Enable only for controlled development testing because it can trigger additional crawl/OpenAI calls. |
| `backend/.env` | `COMPETITOR_REFRESH_DAYS`, `COMPETITOR_REFRESH_RETRY_MINUTES`, `COMPETITOR_REFRESH_POLL_SECONDS` | Optional refresh configuration; template defaults are 7 days, 60 minutes and 60 seconds. |
| `frontend/.env.local` | `NEXT_PUBLIC_API_URL` | Use `http://localhost:8000` for the local FastAPI server. |
| `frontend/.env.local` | `NEXT_PUBLIC_USE_MOCKS` | Defaults to `false` in the D3 template so the frontend talks to FastAPI. Set to `true` only for standalone frontend development with fixtures; mock mode does **not** verify full-stack integration. |
| `frontend/.env.local` | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` | Required for real Clerk sign-in. Leave empty for the local development dashboard shortcut. The publishable key is public; the secret key is not. |
| `frontend/.env.local` | `ALLOW_DEV_DASHBOARD_BYPASS` | Optional; **do not enable in production**. Local `next dev` already permits the development bypass. |

**Minimum local edits for the integrated app:**

```env
# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_USE_MOCKS=false
```

For live AI research, also fill `OPENAI_API_KEY` and `OPENAI_MODEL` in the **root** `.env` and leave `ALLOW_MOCK_CRAWLER=false` in `backend/.env`. API keys are **not required to view the frontend or run offline tests**, but without them you are **not verifying live OpenAI research**.

### 2. Start PostgreSQL and initialize the schema

The simplest reproducible local database uses Docker Desktop. Start Docker Desktop first, ensure port **5432** is free, then run:

```bash
docker run --name scadable-dev-postgres \
  -e POSTGRES_USER=scadable \
  -e POSTGRES_PASSWORD=scadable_local_only \
  -e POSTGRES_DB=live_swot \
  -p 5432:5432 -d postgres:16
docker exec scadable-dev-postgres pg_isready -U scadable -d live_swot
```

This password is an **example for local development only**, never a production secret. In `backend/.env`, set:

```env
DATABASE_URL=postgresql+psycopg://scadable:scadable_local_only@localhost:5432/live_swot
```

**If host port 5432 is in use:** Docker may fail with `bind: address already in use`. A failed `docker run` may still leave the named container behind. Use another available host port (for example **5436**) by running:

```bash
docker rm -f scadable-dev-postgres
docker run --name scadable-dev-postgres \
  -e POSTGRES_USER=scadable \
  -e POSTGRES_PASSWORD=scadable_local_only \
  -e POSTGRES_DB=live_swot \
  -p 5436:5432 -d postgres:16
docker exec scadable-dev-postgres pg_isready -U scadable -d live_swot
```

Then use `DATABASE_URL=postgresql+psycopg://scadable:scadable_local_only@localhost:5436/live_swot` **inside `backend/.env`** (not just as a shell assignment). The container's internal PostgreSQL port remains 5432; the **host** port is 5436. Use the same host port in any PostgreSQL integration-test connection strings below.

If you already run PostgreSQL directly on your machine, create a database called `live_swot` with your existing PostgreSQL user and replace `DATABASE_URL` with its valid connection string instead. Do **not** point test commands at Railway or another shared/production database.

### 3. Install Python dependencies and Chromium

From the repository root, in **Terminal A**:

```bash
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt
python -m pip install ./scraper -e ./llm -e ".[dev]"
mkdir -p scraper/.uv-cache/playwright
PLAYWRIGHT_BROWSERS_PATH="$PWD/scraper/.uv-cache/playwright" python -m playwright install chromium
```

The backend requirements alone do **not** install the separate scraper/LLM packages. The additional `pip` command installs the scraper normally (not editable) and the LLM/repository in editable mode, matching the dependency groups used by CI. Chromium is required for live Crawl4AI research but not for the default offline tests. On Linux, Playwright may additionally need system dependencies; see the [pipeline guide](docs/research-pipeline.md#setup).

**macOS troubleshooting — virtual environment fails during `ensurepip`:** During the D3 fresh-clone test, Homebrew Python 3.12 failed while bootstrapping pip (`truststore/_macos.py`, macOS version detection). The working alternative was `uv`, which can manage a separate Python installation and create the virtual environment without `ensurepip`:

```bash
brew install uv
uv python install 3.12
uv venv --python 3.12 --managed-python --clear backend/.venv
source backend/.venv/bin/activate
uv pip install -r backend/requirements.txt
uv pip install ./scraper -e ./llm -e ".[dev]"
```

Run these from the repository root. Use the `uv` commands **instead of** the failed `python3.12 -m venv` / `pip` installation steps above; you do not need to reinstall the system Python. Once installed, run the same Playwright Chromium command shown above. If `scraper.content_identity` or scraper model exports are missing after switching branches, reinstall the scraper in non-editable mode with `uv pip install --reinstall-package scraper ./scraper` from the repository root. `--clear` only replaces the test checkout's `backend/.venv`.

Initialize the database and launch FastAPI (same Terminal A):

```bash
cd backend
python -m app.db.init_db
uvicorn app.main:app --reload --reload-dir app
```

Confirm the backend works:

```text
http://localhost:8000/health    (should return {"status":"ok"})
http://localhost:8000/docs      (interactive API documentation)
```

Leave Terminal A running. `--reload-dir app` restricts automatic reloads to application source code; otherwise a fresh dependency installation can trigger repeated reloads when Uvicorn watches `.venv`. If initialization fails, first verify Docker/PostgreSQL is running and the `DATABASE_URL` in `backend/.env` matches its credentials. If port 8000 is occupied, stop that local service before launching the backend.

### 4. Start the frontend in a separate terminal

From the repository root, in **Terminal B**:

```bash
cd frontend
npm ci
npm run dev
```

Open **http://localhost:3000**. If you have not configured Clerk, use **Dev only → Go to dashboard** for local development. The profile/competitor screens should request the local FastAPI API because `NEXT_PUBLIC_USE_MOCKS=false`.

If the frontend shows fixtures rather than real backend data, recheck `NEXT_PUBLIC_USE_MOCKS` and restart the Next.js server after changing environment variables. If requests fail, verify `/health` and `CORS_ORIGINS` in `backend/.env`.

### 5. Run the tests and quality checks

Use a **third terminal** or stop the backend process before running backend tests. From the repository root, using the Python virtual environment installed above:

```bash
source backend/.venv/bin/activate
cd backend
DATABASE_URL="sqlite:///:memory:" python -m pytest \
  tests/test_api.py tests/test_orchestrator.py tests/test_overall_swot_review.py \
  tests/test_llm_adapter.py tests/test_costs.py -q
```

This is the standard **offline backend suite used by CI**; the crawler and LLM are mocked where appropriate, so it should not make billable API calls. The repository also has additional backend and pipeline tests; run them separately when needed.

For frontend quality checks (open another terminal at the repository root):

```bash
cd frontend
npm run lint
npx tsc --noEmit
```

For the **PostgreSQL API integration test**, use a dedicated `_test` database; it never uses the application database. After the database container is running, return to the repository root before executing the following commands:

```bash
docker exec scadable-dev-postgres createdb -U scadable live_swot_test
cd backend
source .venv/bin/activate
TEST_DATABASE_URL="postgresql+psycopg://scadable:scadable_local_only@localhost:5432/live_swot_test" \
DATABASE_URL="postgresql+psycopg://scadable:scadable_local_only@localhost:5432/live_swot_test" \
python -m pytest tests/test_api_db_integration.py -q
```

If PostgreSQL was started on **host port 5436**, substitute `5436` for `5432` in the two integration-test URLs above. The integration test uses a **mock crawler/LLM by default**, but persists results through the real PostgreSQL database and API. Do not set `RUN_LIVE_PIPELINE_TEST=1` unless you explicitly want external crawling and billable OpenAI calls. GitHub Actions runs three CI checks: backend tests, frontend ESLint, and LLM/pipeline tests. The PostgreSQL API integration test is not part of the standard PR checks unless separately configured.

### 6. Local setup verification results

**Fresh-clone verification — November 5, 2026 (macOS):** The application was installed in an independent checkout without existing project dependencies or environment files. The setup used Python 3.12.14, Node.js 24.21.0, npm 11.19.0 and Docker 29.5.2. All three environment templates were copied. PostgreSQL started in Docker on host port **5436**, and the tables initialized successfully with `python -m app.db.init_db`. Python dependencies were installed with `uv` after a Homebrew `ensurepip` problem.

FastAPI launched successfully; `/health` returned `{"status":"ok"}` and `/docs` displayed the expected API endpoints. Next.js started, the development dashboard shortcut worked, and the frontend was configured with `NEXT_PUBLIC_USE_MOCKS=false`. `GET /competitors` and `GET /swot/overview` both returned **HTTP 200**, with empty results as expected for a new database.

**Verification scope:** Fresh-clone application startup and local API responses were confirmed. The automated test suite was not rerun in the fresh checkout; automated checks are run separately by CI. Live third-party crawling and OpenAI calls were not part of this setup verification. Authorized OpenAI credentials are needed to test live research.

For deeper component instructions, see [Backend README](backend/README.md), [Frontend README](frontend/README.md), [LLM README](llm/README.md), [Scraper README](scraper/README.md), and the [Research Pipeline Guide](docs/research-pipeline.md).

## Git and GitHub Workflow

Our team uses GitHub for version control and Discord for development coordination.

We follow a feature-branch and pull-request workflow:

1. Team members discuss and divide tasks before beginning development.

2. Each developer creates a separate branch instead of committing directly to `main`.

3. Work is committed and pushed to the developer's branch.

4. The developer opens a pull request targeting `main`.

5. Another team member reviews the changes before merging.

6. Integrated changes are tested before deployment.

We divide responsibilities across frontend development, backend research, LLM analysis, database integration, deployment, and documentation.

This workflow helps our seven-member team avoid conflicting changes, review code quality, and maintain a stable shared branch.

### Deployment Workflow

The frontend is deployed on **Vercel**, while the backend API and PostgreSQL database are hosted on **Railway**.

**D3 frontend:** https://10-scadable.vercel.app/

**D3 competitors page:** https://10-scadable.vercel.app/competitors

**Backend API:** https://backend-d3-production.up.railway.app/

The frontend is hosted on Vercel; the backend API and database are hosted on Railway. GitHub Actions runs three CI checks on pull requests to `main`: backend tests, frontend ESLint, and LLM/pipeline tests. Reviewed pull requests and hosted deployments help keep frontend and backend changes coordinated.

### CI/CD Pipeline and Automated Deployment Evidence

Live SWOT uses **GitHub Actions** for continuous integration and **Railway / Vercel** for deployment.

**Continuous Integration (GitHub Actions)**

The updated D3 CI workflow includes **three checks** on pull requests to `main`:

1. **Backend tests:** Verify API behaviour, service orchestration, validation, and regression coverage.
2. **Frontend linting:** Run ESLint checks for the Next.js/React frontend.
3. **LLM/pipeline tests:** Verify the standalone research pipeline, extraction/comparison schemas, scoring, and related integration logic using test fixtures and mocked external calls.

These checks catch regressions before merge; they do not replace live OpenAI, crawling, or production deployment verification.

![Successful GitHub Actions CI pipeline with backend, frontend, and LLM checks](docs/screenshots/ci-pipeline.png)

**Verification:** [View GitHub Actions workflow runs](https://github.com/csc301-2026-f/10-SCADABLE/actions)

**Backend Automatic Deployment (Railway)**

The FastAPI backend and PostgreSQL database are hosted on Railway. The backend is configured to deploy from the connected GitHub repository when updates reach the deployment branch.

![Railway backend automatic deployment](docs/screenshots/railway-auto-deploy.png)

**Frontend Automatic Deployment (Vercel)**

The Next.js frontend is hosted on Vercel, which is configured to deploy from the connected repository when updates reach the deployment branch.

![Vercel frontend automatic deployment](docs/screenshots/vercel-auto-deploy.png)

## Coding Standards and Guidelines

We follow the existing naming conventions and directory structure of each component.

Pull requests should contain related changes, avoid unnecessary modifications, and be reviewed before merging. We also avoid committing credentials or API keys to the repository.

Tests are updated when relevant functionality changes.

## Non-functional requirements (accessibility & security)

D3 non-functional goals: Lighthouse accessibility **> 80**, keyboard-usable core workflows, labeled forms with accessible errors, validated user input, and no secrets or sensitive stack traces exposed to clients.

### Accessibility

| Requirement | Status / evidence |
| --- | --- |
| Lighthouse accessibility **> 80** | Verified on core Live SWOT screens (Chrome DevTools → Lighthouse → Accessibility): **98** and **96**. Re-check with `npx lighthouse <url> --only-categories=accessibility --view`. |
| Keyboard navigation | Competitor and agent-run lists support Enter/Space; dialogs use Escape and a **focus trap**; profile chart tabs support arrow keys. |
| Form labels and accessible errors | Add Competitor uses visible labels, `aria-invalid` / `aria-describedby`, and `role="alert"` for field and submit errors. |
| Contrast and semantic HTML | App shell uses landmarks (`main`, `nav`), headings, and tables; focus styles via `:focus-visible`. |

![Lighthouse accessibility score 98](docs/screenshots/lighthouse-accessibility-98.png)

![Lighthouse accessibility score 96](docs/screenshots/lighthouse-accessibility-96.png)

### Security

| Requirement | Status / evidence |
| --- | --- |
| Frontend input validation | Add Competitor validates required name/website, length limits, and http(s) URLs before calling the API (`frontend/lib/validation/competitor.ts`). |
| Backend input validation | FastAPI/Pydantic validates create-competitor bodies (name length, sources, `clean_url`). Covered by `backend/tests/test_api.py` (`test_create_competitor_validation`). |
| Secrets not in client or API responses | Browser only receives `NEXT_PUBLIC_*` values and the Clerk publishable key. `CLERK_SECRET_KEY`, database URLs, and OpenAI keys stay in server env. Response schemas do not return credentials. |
| Backend errors without stack traces | Unexpected exceptions return `{"detail": "Internal server error."}` (`backend/app/api/errors.py`). Evidence: `test_unexpected_error_returns_clean_500` in `backend/tests/test_api.py`. |
| Frontend does not show raw server internals | API 5xx bodies are not passed into UI copy; list/profile/runs errors use safe user-facing messages (`frontend/lib/errors.ts`, `frontend/lib/api/client.ts`). |
| Research-run error sanitization | Unexpected research failures are logged server-side, while only generic errors are persisted. API serializers also sanitize previously stored errors in `/runs` and competitor activity. Covered by `test_failed_run_does_not_expose_sensitive_errors` and the orchestrator failure tests. |

## License

Live SWOT was developed as part of CSC301 in collaboration with SCADABLE.

SCADABLE has expressed that our team is free to use and share the work developed for this project and has not requested additional confidentiality restrictions.

SCADABLE follows a mixed licensing approach, maintaining proprietary repositories while selectively releasing projects as open source. Our team intends to keep Live SWOT available for academic collaboration and future development, with any formal open-source licensing decisions made in coordination with the project partner.

## Deliverable History

- **D1:** Interactive frontend prototype and initial project design.
- **D2:** Deployed full-stack application with competitor management, research, persistent data, run monitoring, and initial SWOT output.
- **D3:** Working two-stage LLM extraction/comparison integrated with the backend; competitor and SCADABLE feature-score comparisons; cited competitor SWOT and overall SWOT shown after completed research; operational Agent Runs cost display; improved scraper tracking and analysis reliability; three-part CI (backend, frontend, LLM); and Railway/Vercel deployment. Open Roles extraction and automatic Next Refresh remain incomplete. See [PR #58](https://github.com/csc301-2026-f/10-SCADABLE/pull/58), [PR #63](https://github.com/csc301-2026-f/10-SCADABLE/pull/63), [PR #64](https://github.com/csc301-2026-f/10-SCADABLE/pull/64), and [PR #73](https://github.com/csc301-2026-f/10-SCADABLE/pull/73).

The original D1 prototype remains available [here](https://csc301-2026-f.github.io/10-SCADABLE/deliverables/D1/mockups/final-prototype.html).
