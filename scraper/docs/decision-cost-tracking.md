# Decisions API usage and cost tracking

The scraper can record Decisions API attempts in Langfuse without importing the LLM
analysis package. This is optional telemetry: selection, retries, budget accounting,
Markdown and the schema `2.1` summary keep their existing behavior.

## Installation and activation

From the repository root, install the optional extra with Python 3.12:

```powershell
python -m pip install "./scraper[langfuse]"
```

For standalone development from `scraper/`:

```powershell
uv sync --locked --extra langfuse
uv run --locked --extra langfuse pytest -q
```

The ordinary `uv sync --locked` / `pip install ./scraper` installation does not require
Langfuse. The repository's LLM package independently requires it for analysis telemetry.
After changing scraper source, refresh a root environment's regular scraper installation.

Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and the project's regional
`LANGFUSE_BASE_URL` in the shared repository-root `.env` or process environment. The CLI
and manual runner already load that environment; library callers explicitly call
`scraper.environment.load_environment()` at application startup. Never put keys in
scraper configuration, exports or source control.

Tracking starts only for `selection.mode="llm"` when both trimmed keys are nonempty and
the extra is installed. Missing keys disable tracking. Configured keys without the extra
produce one actionable warning per provider. Structural selection creates no tracking
client or observations. Importing the scraper never initializes Langfuse.

## What is recorded

Each provider creates a `scraper.decisions` parent span. Every dispatched attempt creates
one child generation named `scraper.decisions.candidates` or `scraper.decisions.content`.
Retries are separate observations. Budget exhaustion, oversized requests, or cancellation
while waiting for capacity do not create attempt observations.

Metadata includes component (`scraper_decisions`), endpoint (`/v1/decisions`), model,
prompt version, crawl ID, optional research-run ID, operation, attempt number and dispatch
sequence. Explicit parent IDs keep concurrent crawls separate. Generations have their own
SDK start/end timing and a sanitized outcome such as `success`, `invalid_response`,
`timeout`, `cancelled`, `http_401` or `api_error`.

Only validated API-reported input tokens are sent as `usage_details.input`. Zero is a
valid measured value. Missing or invalid usage is omitted, not replaced with zero or
the local reservation estimate. A response with known usage but invalid answers still
records its known tokens. Output/cache/reasoning counts and dollar costs are not invented.

No prompts, answers, page excerpts, credentials, authorization headers, or exception
bodies are sent by this adapter. This is independent of the existing opt-in local
`decisions.jsonl` diagnostic trace, which can contain request evidence and answers.

Initialization, observation, update and cleanup failures produce sanitized warnings and
do not alter collection. Parent and attempt spans are ended on errors and cancellation.
The shared client is flushed once per provider exit on a worker thread, with a two-second
await limit. A timed-out SDK worker can finish later; this is not a process-exit deadline.
The scraper never shuts down the shared Langfuse client.

## Stable references and companion export

`result.decision_tracking` is a read-only optional `DecisionTrackingReference`. It is
private runtime state: it does not appear in `result.model_dump()`, its JSON schema, or
`crawl_summary.json`. Serializing/deserializing the ordinary result does not retain it.
Use this property directly or read the companion file.

When a completed result has a valid tracking reference, `export_competitor()` also writes:

```text
<competitor>/
  pages/...
  decision_tracking.json
  crawl_summary.json
```

| Companion field | Meaning |
| --- | --- |
| `schema_version` | `1.0` |
| `crawl_id` | Unique per-provider UUID by default |
| `trace_id` | Langfuse trace containing this crawl's Decisions work |
| `observation_id` | Parent span of this crawl's Decisions generations |
| `model` | Actual Decisions model |
| `started_at`, `finished_at` | UTC provider lifetime, useful for bounded queries |
| `research_run_id` | Optional application identifier; omitted when absent |

The default trace ID is deterministically derived from the fresh crawl ID. Callers can
instead supply an existing trace and parent. Identifiers must be nonzero lowercase hex:
32 characters for trace IDs and 16 for parent observation IDs. A parent requires its trace
ID. Invalid parent context disables telemetry rather than attributing calls elsewhere.

The companion is staged and published before `crawl_summary.json`; the summary remains
the completion marker. A companion write/publication failure warns and still permits the
normal export. Existing directories are never overwritten. No companion is written when
tracking is disabled or no valid parent was created. An interrupted crawl that propagates
cancellation does not fabricate a completed bundle, though dispatched observations close.

References prove that local observations were created, not that they reached Langfuse.
`flush()` does not guarantee immediate API read visibility: ingestion is asynchronous.
Treat absent/not-yet-ingested observations as pending or unavailable, not zero cost.

## Backend correlation example

The optional context factory receives `CompetitorInput` once for each competitor handled
by the default provider. Create a fresh context for each crawl:

```python
import asyncio
from pathlib import Path
from uuid import uuid4

from scraper import CompetitorInput, ScraperConfig
from scraper.decision_tracking import DecisionTrackingContext
from scraper.environment import load_environment
from scraper.output import export_competitor
from scraper.service import CompetitorScraper


async def collect_for_run(homepage, run_id, trace_id=None, parent_observation_id=None):
    load_environment()
    service = CompetitorScraper(
        decision_tracking_context_factory=lambda competitor: DecisionTrackingContext(
            research_run_id=str(run_id),
            trace_id=trace_id,
            parent_observation_id=parent_observation_id,
        )
    )
    result = await service.crawl(CompetitorInput(homepage_url=homepage), ScraperConfig())
    await asyncio.to_thread(export_competitor, result, Path("output") / uuid4().hex)
    reference = result.decision_tracking
    # Persist this separately with the research run; no Langfuse keys belong in the DB.
    return reference.model_dump(mode="json", exclude_none=True) if reference else None
```

An existing `decision_provider_factory` keeps its original three positional arguments.
Custom providers own their tracking; the service context factory is not called for them.
Context-factory exceptions are sanitized and fall back to a separate per-crawl trace.

## Costs and retrieval: application responsibilities

Langfuse infers costs from reported usage and a matching model-price definition. Verify
the applicable Decisions endpoint pricing, region and project configuration before relying
on dollar totals. This change does not update prices or query Langfuse's read API.

The existing `decision_usage.estimated_cost_usd` stays available for local budget control.
It includes conservative reservations for uncertain usage; it is not authoritative billing.
Do not add this estimate to Langfuse's cost for the same calls. Keep missing usage and
partial telemetry distinguishable from measured zero when reporting completeness.

Analysing an existing bundle does not execute Decisions again and creates no new scraper
observations. Its companion refers to the original crawl. Later extraction/analysis calls
have their own usage and must be attributed separately.

For retrieval, use Observations V2 with the `usage` field group, trace ID, parent
observation ID, bounded time ranges and cursor pagination. This parent identifies only
the Decisions attempts even when analysis shares the trace. Allow for asynchronous
ingestion and incomplete telemetry. Do not retrieve by timestamp-based names.

References: [Langfuse manual instrumentation](https://langfuse.com/docs/observability/sdk/instrumentation),
[usage and pricing](https://langfuse.com/docs/observability/features/token-and-cost-tracking),
[Observations V2](https://langfuse.com/docs/api-and-data-platform/features/public-api).

## Team handoff for PR #56

| Owner | Remaining work |
| --- | --- |
| PR author | Merge current `main` into `feat/llm-call-cost-tracking`, resolve conflicts, and integrate `decisions-api-cost-tracking` while preserving its small commits. |
| PR author | Install local `./llm` in CI and audit packaging/setup instructions. Current CI cannot resolve `scadable-llm==0.1.0` from the registry. |
| LLM team | Keep correlation outside required analysis domain fields. Repair `CompanyAnalysis` backend construction missing `crawl_time_string`, and fake clients rejecting `observation_name`. |
| LLM team | Link analysis/comparison observations to the shared run context; test usage accounting, missing usage, telemetry errors and cleanup. |
| Backend team | Supply research-run context, persist the runtime reference or companion contents, and query costs asynchronously by IDs. |
| Backend team | Store Decisions, analysis and comparison costs without double counting; distinguish pending, unavailable, partial and zero. Define attribution for comparisons spanning competitors. |
| Langfuse owner | Verify project/region and Decisions-specific model pricing; validate totals against reported usage. |
| Frontend team | Display persisted costs and availability states; keep Langfuse credentials and retrieval server-side. |
| Team | Re-run backend adapters and integration checks, then perform one separately authorized live run covering both scraper and analysis. |

Offline verification uses fake OpenAI and Langfuse clients. With the extra installed, an
additional real-SDK test exports only to memory and forbids HTTP. Browser checks remain
opt-in. No paid calls are required. The PR's inherited backend adapter failures are outside
this scraper change; a successful root suite alone does not establish backend compatibility.
