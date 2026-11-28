# Live SWOT backend

FastAPI, SQLAlchemy and PostgreSQL coordinate crawler and LLM adapters, competitor research, scheduled refresh, GitHub feature sync and costs. See [developer guide](../DEVELOPERS.md) for complete local setup, testing, configuration, deployment and troubleshooting.

From the repository root run `./setup.sh`, then `cd backend && .venv/bin/python -m uvicorn app.main:app --reload --reload-dir app`. Use Python 3.12 and the sibling scraper and LLM packages. Interactive API documentation: `http://localhost:8000/docs`; schema: `/openapi.json`; liveness: `/health`. Research is asynchronous; a successful individual run can precede comparison and cost updates.

Build with repository root context: `docker build -f Dockerfile -t live-swot-api .` (run at root). Configure Railway with the root Dockerfile and environment-specific database URL; never build with `backend/` alone as context. The startup command creates missing tables, not migrations.

Run offline tests from `backend/` with `DATABASE_URL=sqlite:///:memory: RUN_LIVE_PIPELINE_TEST=0 .venv/bin/python -m pytest tests/ -q`. Use a disposable PostgreSQL `_test` database for integration tests as described in the developer guide.

For GitHub App setup and manifest validation see [GitHub setup](docs/github-app-setup.md). For scraper/LLM contracts see [research pipeline](../docs/research-pipeline.md). The [main README](../README.md) covers the current product workflows and NFR evidence.
