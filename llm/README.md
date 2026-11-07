# SWOT analysis LLM

Python package that turns the scraper's exported Markdown pages into cited company analysis
and a SCADABLE-versus-competitors SWOT. It calls the OpenAI Responses API with Pydantic
structured outputs and never crawls by itself: collection belongs to the
[scraper](../scraper/README.md).

The full pipeline guide, including browser setup and failure handling, is
[docs/research-pipeline.md](../docs/research-pipeline.md).

## How it works

```text
scraper export: crawl_summary.json + pages/*.md
  -> load_analysis_input()          read and validate the bundle
  -> Analyzer.extract_information() summary, categorized claims, job postings, conflicting data (1 call)
  -> Analyzer.analyze_company()     strengths, weaknesses, workforce changes (1 call)
  -> Analyzer.compare()             SWOT entries, feature scores, competitor notes,
                                    across SCADABLE and all competitors (1 call)
```

Each company costs two model calls; a comparison adds one call over all companies.

## Setup

Use Python 3.12. From the repository root, install the scraper first (it is not on PyPI),
then this package:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install ./scraper -e "./llm[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

On macOS/Linux, activate with `source .venv/bin/activate`. `-e` (editable) makes your edits
to `llm/` take effect without reinstalling; `[dev]` adds the test tools. The package installs
as `scadable-llm`, but imports stay `llm.*`.

The scraper is installed as a regular (non-editable) copy: an editable install would
collide with the outer `scraper/` folder on imports. Reinstall `./scraper` after changing
its code.

This folder's `pyproject.toml` is the only place `llm` is packaged. The root
`pyproject.toml` packages nothing; it depends on `scraper` and `scadable-llm` and holds the
pytest settings. uv reads both packages' local paths from its `[tool.uv.sources]`; pip
needs the folders named in the install command, as above.

## Configuration

Settings come from the repository-root `.env` (see [.env.example](../.env.example)) or the
process environment.

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Required. Shared with the scraper's page-selection calls |
| `OPENAI_MODEL` | Required. Analysis model; must support Responses API structured outputs |
| `OPENAI_TIMEOUT_SECONDS` | Request timeout, default 30 |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Optional. Cost tracking is on only when both are set |
| `LANGFUSE_BASE_URL` | Langfuse region URL, e.g. `https://us.cloud.langfuse.com` |

Never commit `.env`. Only `.env.example`, with empty key values, belongs in git.

## Running it

Analyze one or more companies, crawling first (needs Chromium; see the
[pipeline guide](../docs/research-pipeline.md#setup)):

```powershell
python examples/research_pipeline.py https://cluely.com/ --max-pages 3
```

Analyze a crawl that is already saved, without crawling again:

```powershell
python examples/research_pipeline.py --export scraper/output/<run>/<site>/crawl_summary.json
```

Both print JSON results. They run extraction and company analysis only; `compare()` is
reached through `run_comparison()` or by calling it directly.

From Python, the caller creates the client and must close it:

```python
from llm.analyzer import Analyzer
from llm.client import OpenAIClient
from llm.config import Settings
from llm.pipeline import analyze_export

async def analyze(summary_path):
    client = OpenAIClient(Settings())
    try:
        return await analyze_export(summary_path, analyzer=Analyzer(client))
    finally:
        await client.close()
```

Run it with `asyncio.run(analyze("path/to/crawl_summary.json"))` from a script.

| Function (`llm.pipeline`) | Does |
| --- | --- |
| `analyze_export(summary_path, analyzer=...)` | Analyze a saved bundle; returns `CompetitorResearchResult` |
| `run_pipeline(competitors, output_directory=..., analyzer=...)` | Crawl, export and analyze several companies |
| `run_competitor(...)` / `run_scadable(...)` | The same for one company |
| `run_comparison(scadable, competitors=..., ...)` | Crawl and analyze everyone, then `compare()`; returns `SWOTAnalysisByURL` |

## Output

- `ExtractedInformationByURL`: the summary with `summary_page_urls`, `job_postings`,
  `claims_cited`, each claim with a `category` and a `claim_page_url`, and `conflicting_data`, containing the company name, the original claim (with a cited URL), and the claim which differs from it (with a cited URL). 
- `CompanyAnalysis`: `company_name`, `summary` with `summary_page_urls`, `claims_cited`, `conflicting_data`,  and `features`. In `claims_cited`, the `workforce_improvement`, `workforce_decline`, `strength`, and `weakness` are what the LLM was able to infer from the `claims_cited` in `ExtractedInformationByURL`.
- `SWOTAnalysisByURL`: `SWOT_entries` (strength, weakness, opportunity or threat, each with
  its supporting page URLs), `scores` (0-10 per competitor feature), `conflicting_data`, and `competitor_notes`.

`ExtractedInformationByURL` and `CompanyAnalysis` also carry `crawl_time_string`: the
crawl's finish time as `YYYY-MM-DD HH:MM:SS`, copied from the scraper's `crawl_summary`.

## Citations

The model never sees or writes URLs. Each prompt numbers its pages from 0, the model cites
those numbers, and the code checks every number is in range before converting it back to a
URL. An out-of-range citation raises an error instead of producing a made-up link. Schema
classes come in pairs for this: `...ByIndex` is what the model returns, `...ByURL` is what
callers get.

Schemas must stay compatible with OpenAI strict mode: every field required, no open-ended
dicts, and numeric limits checked with validators rather than `Field(ge=..., le=...)`.

## Cost tracking

`OpenAIClient` records every call as a Langfuse generation with its model and token usage
(input, cached input, output and reasoning tokens). Langfuse computes the cost from its model
prices, in USD. Generations are named after the company, step and crawl time, for example
`Cluely-extraction-2026-11-02 19:45:14`, `Cluely-analysis-...` and `SWOT-...` (SWOT is the exception since there are multiple companies, so SWOT tracing has the timestamp of when the full comparison pipeline was initiated).

`close()` flushes pending data, so always close the client. Without Langfuse keys, tracing
is off and nothing is sent. The scraper's own page-selection calls do not go through this
client. Optional `scraper[langfuse]` instrumentation records them separately; see the
[Decisions tracking guide](../scraper/docs/decision-cost-tracking.md) for setup and correlation.

## Tests

From the repository root:

```powershell
python -m pytest tests
```

The tests make no real OpenAI or Langfuse requests. Most use stub clients; the client and
integration tests run the real `OpenAIClient` against a mocked network transport.

## Files

| File | Contents |
| --- | --- |
| `analyzer.py` | `Analyzer`: extraction, company analysis, comparison, citation checks |
| `client.py` | `OpenAIClient`: structured-output requests, prompt caching, Langfuse tracking |
| `config.py` | `Settings` read from `.env` |
| `inputs.py` | `load_analysis_input()`: reads a scraper export |
| `pipeline.py` | Crawl-then-analyze orchestration |
| `schemas.py` | Pydantic input and output models |
| `swot_entries.py` | Flattens a `CompanyAnalysis` into rows for the backend |
| `prompts/` | System and user prompts for each step |
| `pyproject.toml` | Package definition (`scadable-llm`) and dependencies |

## Known gaps

- Full page Markdown is sent without truncation; very large sites can exceed the model's
  context limit.
