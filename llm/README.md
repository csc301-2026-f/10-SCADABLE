# SWOT analysis LLM

Python package that turns the scraper's exported Markdown pages into cited company analysis
and a SCADABLE-versus-competitors SWOT. It uses the OpenAI Responses and Conversations APIs
with Pydantic structured outputs and never crawls by itself: collection belongs to the
[scraper](../scraper/README.md).

The full pipeline guide, including browser setup and failure handling, is
[docs/research-pipeline.md](../docs/research-pipeline.md).

## How it works

```text
scraper export: crawl_summary.json + pages/*.md
  -> load_analysis_input()           read and validate the bundle
  -> Analyzer.extract_information()  summary, claims, job postings, conflicting data
  -> Analyzer.compare()              SWOTs, feature scores and pairs, competitor notes
```

Each step generates an output, which a separate validator call and code checks review.
Problems are sent back for a revision, and only the flagged fields are replaced
(`scores` and `features` are replaced together).

`OPENAI_CALL_LIMIT` caps generation attempts per step. Validator calls are extra, so the
default of 3 means 2 to 6 model calls per step, unless an error stops it early.

Each step uses its own OpenAI conversation, so its messages are stored by OpenAI. Later
attempts are billed for the earlier history as input. Validator calls use no conversation.

If problems remain after the last attempt, out-of-range extraction citations and SWOT code
violations raise an error. Remaining validator feedback does not.

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
| `OPENAI_CALL_LIMIT` | Maximum generation attempts per step, including the first; default 3 |
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

Both print JSON results and run extraction only.

To run extraction and then `compare()`, use `examples/compare_companies.py`. With saved
crawls, it skips crawling but runs extraction again:

```powershell
python examples/compare_companies.py --scadable-export <SCADABLE crawl_summary.json> --export <competitor crawl_summary.json>
```

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
| `run_pipeline(competitors, output_directory=..., analyzer=...)` | Crawl, export and run extraction for several companies |
| `run_competitor(...)` / `run_scadable(...)` | The same for one company |
| `run_comparison(scadable, competitors=..., ...)` | Crawl and analyze everyone, then `compare()`; returns `SWOTAnalysisByURL` |

## Output

- `ExtractedInformationByURL`: `company_name`, `summary` with `summary_page_urls`,
  `job_postings`, `claims_cited` (each with a category and page URL), `conflicting_data`,
  and `crawl_time_string` (the crawl's finish time as `YYYY-MM-DD HH:MM:SS`). The extraction
  prompt asks for the categories `feature`, `target_customers`, `pricing`, `integrations`
  and `differentiator`.
- `SWOTAnalysisByURL`: `SWOT_entries` (strength, weakness, opportunity or threat, each with
  supporting page URLs), `scores` (0-10, for SCADABLE and competitor features), `features`
  (feature pairs), `competitor_swots`, `competitor_notes` and `conflicting_data`.

## Citations

The model cites pages by number instead of URL. Extraction numbers the scraped pages from 0.
Comparison numbers each company's claim and job posting pages separately, and a SWOT entry
can cite either. Code checks the cited numbers before converting them to URLs. The validator
reviews whether the sources support the output, but it can miss problems. Schema classes
come in pairs: `...ByIndex` uses page or company numbers for prompts and model output, and
`...ByURL` is what callers get.

Schemas must stay compatible with OpenAI strict mode: every field required, no open-ended
dicts, and numeric limits checked with validators rather than `Field(ge=..., le=...)`.

## Cost tracking

`OpenAIClient` records each model response as a Langfuse generation with its model and token
usage (input, cached input, output and reasoning tokens). Langfuse computes the cost from its
model prices, in USD. Generation names include the step and attempt number, and validator
calls have their own. Each step has a parent span.

The backend must pass a `session_id` to `Analyzer` for Langfuse session tracking, using the
same ID for extraction and comparison in a run. Without one, the analyzer sets no session ID.
This also applies to standalone scripts. The code does not enforce this requirement.

Backend run cost totals are not updated yet: the lookup still searches for generations by
their old exact names. Until it finds a run's calls by session ID, totals are incomplete.

Only validator requests set an explicit prompt cache breakpoint.

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
| `analyzer.py` | `Analyzer`: extraction, comparison and revision loops |
| `validators.py` | Citation and score checks, and the LLM validator call |
| `client.py` | `OpenAIClient`: structured-output requests, conversations, prompt caching, Langfuse tracking |
| `config.py` | `Settings` read from `.env` |
| `inputs.py` | `load_analysis_input()`: reads a scraper export |
| `pipeline.py` | Crawl-then-analyze orchestration |
| `schemas.py` | Pydantic input and output models |
| `swot_entries.py` | Flattens `ExtractedInformationByURL` into rows for the backend |
| `prompts/` | System and user prompts for each step and its validator |
| `pyproject.toml` | Package definition (`scadable-llm`) and dependencies |

## Known gaps

- Full page Markdown is sent without truncation; very large sites can exceed the model's
  context limit.
- Every extraction validation call resends the scraped pages, which adds to its cost.
- The backend does not yet pass a `session_id` to `Analyzer`.
- Backend run cost totals still look up generations by their old names.
- `to_swot_entries()` keeps only strength and weakness claims, which the extraction prompt
  does not ask for.
