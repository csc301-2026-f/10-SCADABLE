# Live SWOT backend

## Run locally

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
cp .env.example .env   # set DATABASE_URL
uvicorn app.main:app --reload
```

Interactive docs: http://localhost:8000/docs

## Docker

```bash
docker build -t live-swot-api backend
docker run --env-file backend/.env -p 8000:8000 live-swot-api
```

The container creates any missing tables (`app.db.init_db`) and then starts
uvicorn on `$PORT` (default 8000). On Railway, set the service root directory
to `backend` and add `DATABASE_URL` and `CORS_ORIGINS` as variables.

## API

Responses use camelCase keys and string IDs to match `frontend/lib/api/types.ts`.
Errors are always `{"detail": "<message>"}`.

| Method | Path | Returns | Errors |
|---|---|---|---|
| GET | `/health` | `{"status": "ok"}` | |
| GET | `/competitors?skip=&limit=` | `CompetitorSummary[]` | 422 bad pagination |
| GET | `/competitors/{id}` | `CompetitorProfile` | 404 |
| POST | `/competitors` body `{name, dataSources[]}` | 201 `{competitor, queuedRunId}` | 409 duplicate site, 422 invalid input |
| DELETE | `/competitors/{id}` | 204 | 404 |
| POST | `/competitors/{id}/rerun` | 202 `CompetitorProfile` (status `pending`) | 404, 409 already pending/running |
| GET | `/runs?competitorId=&limit=` | `AgentRun[]` (newest first) | 422 |
| GET | `/runs/{id}` | `AgentRun` | 404 |
| GET | `/swot?competitorId=` | `SwotOverview` (one competitor's latest SWOT, or the overview without `competitorId`) | 404, 422 |
| GET | `/swot/overview` | `SwotOverview` | |
| GET | `/company` | `CompanyProfile` | |

Notes:
- Research status `completed` in the DB is returned as `success`.
- Only the first entry of `dataSources` is stored (as the website) for now.
- `trend`, `chartSeries`, `openRoles`, `costUsd` are placeholders until the
  pipeline produces that data.
- `/swot/overview` uses the overall SWOT when one exists, otherwise it combines
  each competitor's latest SWOT.

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest
```

## Automatic competitor research refreshes

The backend calculates `nextRefreshAt` from persisted run history. When automatic
refreshes are **enabled**, the profile shows the next scheduled attempt. A
successful research run schedules the next check in 7 days by default. A failed
run retries after 60 minutes; previous successful SWOT/analysis remains stored.
While a run is pending/running, `nextRefreshAt` is `null`.

The scheduler is **off by default** to avoid unplanned OpenAI/scraping costs.
To enable it on Railway or locally, set:

```env
AUTO_REFRESH_ENABLED=true
COMPETITOR_REFRESH_DAYS=7
COMPETITOR_REFRESH_RETRY_MINUTES=60
COMPETITOR_REFRESH_POLL_SECONDS=60
```

Keep the API running (on an always-on Railway instance); the in-process loop
checks the PostgreSQL database every 60 seconds. It uses row locks and a
single active run per competitor to avoid duplicate refreshes across replicas.
Runs interrupted by a restart are marked failed after a 30-minute stale window
and retried using the normal retry interval. No DB migration is needed;
schedule state comes from the existing `research_runs` table. A stopped service
cannot execute scheduled work while it is offline and will catch up on startup.

The backend supports an optional `scadable_score` value per feature from the
comparison output. Existing LLM schemas/prompts are unchanged by this work,
so current comparison results continue returning `scadable: null` until the
LLM team implements and supplies evidence-backed SCADABLE ratings. No scores
are estimated or fabricated by the API.
