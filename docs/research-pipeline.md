# Scraper and LLM research pipeline

For connecting this pipeline to the FastAPI backend on `main`, see the
[backend integration plan](superpowers/plans/2026-10-20-backend-integration.md).

The scraper owns discovery, Decisions scoring, rendering, Markdown cleanup, and export.
The separate LLM layer consumes those exports to extract cited facts and produce company
analysis. No API server, database, scheduler, or frontend integration is included.

```text
CompetitorInput URLs
  -> CompetitorScraper.crawl_many() + Decisions
  -> export_competitor(): crawl_summary.json + pages/*.md
  -> load_analysis_input(): validated metadata + exact Markdown
  -> Analyzer.extract_information() -> Analyzer.analyze_company()
  -> CompetitorResearchResult: crawl provenance + facts + analysis
  -> (optional, across companies) Analyzer.compare(): cited SWOT entries, feature scores, notes
```

## Setup

Use Python 3.12. From the repository root, install both local packages together:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install ./scraper -e "./llm[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

On macOS/Linux, activate with `source .venv/bin/activate` and create `.env` from
`.env.example` if absent.

Each layer is its own package: `scraper/pyproject.toml` builds `scraper`, and
`llm/pyproject.toml` builds `scadable-llm` (imported as `llm`). The root `pyproject.toml`
packages nothing; it depends on both and holds the pytest settings. Neither package is on
PyPI, so pip must be given both folders in one command. To also install the root project,
add it to the same command (`python -m pip install ./scraper -e "./llm[dev]" -e ".[dev]"`).
The root's uv source configuration points to both local folders.

The scraper is installed as a regular package to avoid an editable import collision with
the outer `scraper/` directory. Reinstall `./scraper` after changing its source. The `llm`
package is editable, so its changes apply without reinstalling. The scraper's independent,
editable development setup remains in [scraper/README.md](../scraper/README.md). The LLM
layer's own guide is [llm/README.md](../llm/README.md).

Both layers default to the same repository-root `.env`, even when launched from `scraper/`
or another nested directory. `scraper/.env` is no longer used. Set `OPENAI_API_KEY` and
`OPENAI_MODEL` in the root file or the process environment. The analysis
model must support Responses API structured outputs. Tests and example `--help` need no
real credentials. Offline tests also need no browser binaries.

For live crawling, install Chromium using the interpreter that runs the pipeline.
The browser boundary now applies the same cache setup as `manual_test.py`: in this
checkout it defaults the SDK cache to `scraper/.uv-cache` and reuses
`scraper/.uv-cache/playwright` when that directory exists. This applies to the CLI,
example, and direct Python consumers. Explicit environment settings take precedence.

For a fresh browser installation into the checkout cache, run from the repository root:

```powershell
$env:CRAWL4_AI_BASE_DIRECTORY = Join-Path (Get-Location) "scraper/.uv-cache"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) "scraper/.uv-cache/playwright"
python -m playwright install chromium
```

See the [scraper quickstart](../scraper/docs/quickstart.md) for browser and site-specific setup.

An older browser in the global Windows cache cannot satisfy a newer Playwright revision.
If startup reports a missing executable, use the reported interpreter to install Chromium
with the same `PLAYWRIGHT_BROWSERS_PATH` used at runtime. A separate virtual environment
does not automatically reuse another environment's browser location.

For a deployed backend, install Chromium during image/deployment setup. Set
`PLAYWRIGHT_BROWSERS_PATH` consistently during installation and execution, and use a
writable `CRAWL4_AI_BASE_DIRECTORY`. Linux images also need Playwright's system dependencies
(install with `python -m playwright install --with-deps chromium`). Browser downloads are
never performed automatically during a research request. Standalone installs outside this
checkout retain SDK defaults unless the host sets these variables.

## Run the example

Analyze a published export without crawling again:

```powershell
python examples/research_pipeline.py --export scraper/output/run-001/example.com/crawl_summary.json
```

Or replace this example URL with a competitor homepage to collect and analyze fresh content:

```powershell
python examples/research_pipeline.py https://competitor.example/ --max-pages 3
```

Multiple homepage arguments are supported. By default, each invocation chooses a fresh
UTC-named directory under `scraper/output/`. Use `--output` to choose a fresh run root,
`--env-file` to select a credential file, or `--selection-mode structural` to disable only
the scraper's model requests. Downstream analysis still uses OpenAI.

The example prints a JSON array of research results. Page Markdown and `crawl_summary.json`
keep the scraper's existing output structure; analysis does not rewrite those files.
Progress logs go to stderr; stdout remains the JSON result.

## Python interfaces

The top-level `scraper/` and `llm/` directories keep collection and analysis separate.
`llm/` is packaged by its own `pyproject.toml` as `scadable-llm`; analysis imports use
`llm.*`, including `llm.config` for settings and `llm.pipeline` for orchestration.

```text
scraper/           Standalone collection package and documentation
llm/
  pyproject.toml   Package definition (scadable-llm)
  config.py        Analysis environment settings
  pipeline.py      Scraper export and analysis orchestration
  inputs.py        Markdown bundle loader
  analyzer.py      Extraction, company analysis, and comparison
  client.py        OpenAI structured-output client
  schemas.py       Analysis input and output contracts
  swot_entries.py  Source-linked SWOT rows
  prompts/         Extraction, analysis, and comparison instructions
```

| Interface | Purpose and return value |
| --- | --- |
| `load_analysis_input(summary_path)` | Synchronously read a validated bundle into `AnalysisInput` |
| `await analyze_export(summary_path, analyzer=...)` | Analyze a saved bundle and return a `CompetitorResearchResult` (SCADABLE is also treated like a "Competitor" as well, calling "analyze_company()" on SCADABLE). |
| `await run_pipeline(competitors, output_directory=..., analyzer=...)` | Collect and analyze a sequence of `CompetitorInput`; return research results in crawl-completion order |
| `await run_competitor(competitor, ...)` / `await run_scadable(scadable, ...)` | Return one `CompetitorResearchResult`. Both functions use the same "analyze_company()" |
| `await run_comparison(scadable, competitors=..., ...)` | Collect and analyze every company through one shared crawl, then call `Analyzer.compare`. It returns a `SWOTAnalysisByURL`: cited SWOT entries, per-competitor feature scores and competitor notes. It 
recrawls every company on each call to ensure the data is up to date.|

The collection functions accept optional `scraper_settings: ScraperConfig` and an injected
`scraper: ScraperService`. `run_pipeline(..., scadable=...)` includes SCADABLE in the same
shared crawl and returns its company result alongside the competitors. Existing analysis
results can be compared directly with `Analyzer.compare(ComparisonInput(...))` without
another crawl. `Analyzer.compare(ComparisonInput(...))` returns that same `SWOTAnalysisByURL`.

The caller chooses credentials and owns the analysis client's lifetime:

```python
from llm.analyzer import Analyzer
from llm.client import OpenAIClient
from llm.config import Settings
from llm.pipeline import run_pipeline
from scraper import CompetitorInput, ScraperConfig
from scraper.environment import load_environment


async def research():
    load_environment()  # Shared repository-root .env; process values win.
    client = OpenAIClient(Settings(_env_file=None))
    try:
        return await run_pipeline(
            [
                CompetitorInput(
                    homepage_url="https://competitor.example/", name="Example"
                )
            ],
            output_directory="scraper/output/run-001",  # Choose a fresh root.
            scraper_settings=ScraperConfig(max_pages=3),
            analyzer=Analyzer(client),
        )
    finally:
        await client.close()
```

Await `research()` in an existing event loop. A standalone script can use
`asyncio.run(research())`. The pipeline moves export/read operations to worker threads;
it does not start a nested event loop or close an injected analysis client.

## Configuration and results

| Configuration | Owner |
| --- | --- |
| `ScraperConfig` / `SelectionConfig`, including TOML overrides | Collection policy, limits, Decisions model, and estimated Decisions budget |
| `OPENAI_API_KEY` | Shared credential for Decisions and downstream analysis |
| `OPENAI_MODEL` | Downstream analysis model; does not change the Decisions model |
| `OPENAI_TIMEOUT_SECONDS` | Analysis SDK request timeout; default 30 seconds |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | Optional analysis cost tracking; on only when both are set |
| `LANGFUSE_BASE_URL` | Langfuse region URL, e.g. `https://us.cloud.langfuse.com` |

`Settings()` and `load_environment()` use the same repository-root `.env` by default.
The resolver identifies the checkout from its `pyproject.toml`, `scraper/pyproject.toml`,
and `llm/` directory, checking the working directory's ancestors and the scraper module's
ancestors. Without a checkout, installed packages default to the working directory's `.env`.
Calling `load_environment(path)`
explicitly and then `Settings(_env_file=None)` lets an importing application choose one
credential source. Process environment variables take precedence. The old provider-selector
settings have been removed; collection always uses the standalone scraper.

Each `CompetitorResearchResult` contains:

- `crawl_summary`: the unchanged schema `2.1` scraper index, including source/final URLs,
  page file references, titles, categories, UTC timestamps, counts, warnings, and Decisions usage.
- `extracted_information`: an `ExtractedInformationByURL` with the summary and its source page URLs, job postings (with citations), and categorized claims, each with a `claim_page_url`.
- `analysis`: the existing `CompanyAnalysis` with cited strengths/weaknesses, cited workforce improvements/declines.

Serialize with `result.model_dump(mode="json")` or `result.model_dump_json()`. The
[scraper output guide](../scraper/docs/output-layout.md) remains the authority for filenames,
completion markers, provenance, and no-overwrite behavior.

## SWOT comparison output

`Analyzer.compare()` returns a `SWOTAnalysisByURL`:

- `SWOT_entries`: `component` (strength, weakness, opportunity or threat), `what`, and
  `swot_component_page_urls` (the supporting page URLs, at least one per entry).
- `scores`: `company_name`, `feature` and `score` (0-10) for each competitor feature.
- `competitor_notes`: one note per competitor, in input order.

Each company costs two model calls (extraction and analysis); the comparison adds one call over
all companies. With Langfuse keys set, `OpenAIClient` records each of these calls as a Langfuse
generation with its token usage, and Langfuse computes the cost in USD. The scraper's
Decisions calls do not go through this client. With `scraper[langfuse]` installed, the
scraper records them separately using the same Langfuse environment settings; see the
[Decisions tracking guide](../scraper/docs/decision-cost-tracking.md) for correlation
references and backend cost-retrieval responsibilities.

## Failure handling and current limits

- The loader requires a valid completion index and follows its `markdown_file` references.
  It analyzes successful, nonempty pages; failed and blank-page records remain in the summary.
  Partial runs with usable content retain their status and warnings.
- No usable pages, invalid indexes, missing files, or invalid UTF-8 fail before an analysis
  request. Existing competitor output folders and conflicting competitor folder names are rejected
  before crawling.
- An unusable export error includes its competitor, URL, status, saved index, crawl warnings,
  and page failures. A missing browser includes the executing interpreter, cache setting,
  and install command, instead of being replaced by a generic empty-content error.
- To reduce likelihood of false citation generations, the model cites its information by nonnegative integer 
  (page index) assigned to each page. After the model responds, each page index is then converted back to its 
  corresponding URL before returning the final result. A page number outside its range, a SWOT entry with no 
  citation, a strength or weakness that doesn't cite a SCADABLE page, or a feature score for SCADABLE fails 
  before any result is built. The prompts show page numbers, titles and categories, not URLs, and redirect destinations
  remain metadata.
- In each layer of the analysis, a new "index directory" is created to map all the provided pages with page indexes, starting 
from 0, ending at n-1 for the provided n pages.
- A batch fails fast when a company has no usable content or export/analysis raises.
  The crawl iterator is closed when supported, including on cancellation. Published exports
  remain available for `analyze_export()` retries; cancellation does not roll back files.
- Browser startup and close complete their bounded SDK work before caller cancellation
  propagates. Startup uses `page_timeout_seconds`; close has a 10-second deadline.
  Hosts should await cancellation so owned browser drivers finish before loop shutdown.
- Full Markdown is supplied without chunking or truncation. Model context-limit errors
  propagate. The Decisions allowance applies only to selection, not downstream analysis costs.

## Verification

From the repository root, using the combined environment:

```powershell
python -m pytest tests -q
python -m pytest -c scraper/pyproject.toml scraper/tests -q
python -m pytest tests/test_research_integration.py -q
python examples/research_pipeline.py --help
```

The integration test uses the real service, Decisions adapter, exporter, loader, analyzer,
and SDK parsing with browser/HTTP boundaries mocked. It verifies exact Markdown, metadata,
citations, client cleanup, page-number validation, and replay without recrawling. Other tests cover invalid bundles,
configuration, comparison ordering, cancellation, schemas, and score bounds.
Opt-in Chromium tests and standalone checks remain in the scraper's own documentation.

To test the real browser pipeline against loopback pages, without paid model requests:

```powershell
$env:SCRAPER_BROWSER_TESTS = "1"
python -m pytest tests/test_research_browser.py -q
```

These subprocess tests cover three competitors, exact exported Markdown passed to the
analysis boundary, missing browser binaries, startup cancellation, and absence of pending
tasks or closed-loop transport errors on process exit.
