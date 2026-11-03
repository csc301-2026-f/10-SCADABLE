# Competitor scraper

Standalone Python package for discovering and collecting source-linked competitor content.
It runs independently of the backend, database, downstream analysis and frontend.

The repository's [research pipeline](../docs/research-pipeline.md) consumes this package's
published Markdown bundles for cited LLM analysis. Collection and analysis remain separate.

Start with the [architecture and integration guide](docs/SCRAPER_ARCHITECTURE.md) for the
component flow, Decisions scoring, Python interfaces and backend/LLM handoff. Use the
[quickstart](docs/quickstart.md) for setup, editable manual tests and inspecting outputs.

Provide homepage URLs. The scraper combines sitemap and internal-link discovery,
BestFirst/exploration or BFS, Crawl4AI rendering, and conservative Markdown extraction.
Each website produces cleaned page files and a compact `crawl_summary.json` index.
Existing output directories are never overwritten.

LLM selection is the default. Set `OPENAI_API_KEY` in the shared repository-root `.env`
(copy [../.env.example](../.env.example) to `../.env` when working from `scraper/`
if needed), or select `--selection-mode structural` for API-free crawling. There are no
HTTP endpoints or downstream company-analysis prompts in this package.

## Setup and checks


Use Python 3.12 and [uv](https://docs.astral.sh/uv/). From the repository root:

```powershell
cd scraper
uv sync --locked
uv run --locked python -m playwright install chromium
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
```

For pip users, runtime installation is also supported:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
```

The pip path installs runtime dependencies; uv is the supported way to reproduce the
locked development environment. Offline tests and structural crawling need no API key;
LLM-guided crawling requires `OPENAI_API_KEY`. Python 3.12 is the supported baseline.
No terminal key assignment is needed when using `.env`. Direct Python consumers can
call `scraper.environment.load_environment(path)` during application startup.

The default tests do not require browser binaries. Real crawling requires Chromium.
On Linux, Playwright may also require OS libraries. See the official
[Crawl4AI installation guide](https://docs.crawl4ai.com/core/installation/).

Crawl4AI is pinned to 0.9.5. Pydantic provides validated, JSON-compatible contracts;
`tomllib` and the CLI's `argparse` come from Python. Crawl4AI itself brings
LLM-related transitive dependencies. OpenAI is now also a direct dependency for the separate
Decisions adapter used by adaptive traversal. Lxml supplies network-free HTML context parsing.

## Documentation

| Guide | Contents |
| --- | --- |
| [Architecture and integration](docs/SCRAPER_ARCHITECTURE.md) | Start here: workflow, Decisions, Python/FastAPI integration, settings and limits |
| [Quickstart](docs/quickstart.md) | Installation, manual runner, CLI and output inspection |
| [Decisions](docs/decisions.md) | Prompts, adaptive selection, credentials, usage and fallback |
| [Decisions cost tracking](docs/decision-cost-tracking.md) | Optional Langfuse setup, correlation references and team handoff |
| [Discovery](docs/discovery.md) | URL policy, frontier, sitemap and robots behavior |
| [Extraction](docs/extraction-and-output.md) | Browser interactions, Markdown cleanup and categorization |
| [Output layout](docs/output-layout.md) | File naming, schema, provenance and publication |
| [Integration and reliability](docs/integration.md) | Async consumers, retries, deadlines and cancellation |
| [Migration](docs/migration.md) | Removed keyword settings and schema changes |
| [Selection verification](docs/decision-validation.md) | Synthetic scenarios and live comparison procedure |

## Project files

- `manual_test.py`: edit websites, settings and output location, then run with the project interpreter.
- `config.example.toml`: global settings and per-competitor overrides.
- `src/`: installed as the `scraper` Python package; `service.py` is the async entry point.
- `tests/`: mocked/offline tests and opt-in Chromium tests using loopback servers.
- `output/`: ignored generated bundles and diagnostics. Use a fresh run directory each time.

Source dependencies and reproducible versions are defined in `pyproject.toml` and `uv.lock`.
Generated output, environments, caches, build artifacts and credentials are ignored by Git.
