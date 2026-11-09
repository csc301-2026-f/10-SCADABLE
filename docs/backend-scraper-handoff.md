# Backend handoff: scraper optimizations and cost tracking

The scraper optimizations fit the existing research workflow. Analysis deduplication,
source citations, and job-posting persistence are already connected. The main remaining
integration work is linking scraper telemetry to backend runs and including its costs.

## 1. Connect scraper tracking to each research run

**Why:** Decisions calls currently have their own crawl references, but the backend does
not supply its research-run ID or save those references in the run snapshot.

**Files and functions:**

- [orchestrator.py](../backend/app/services/orchestrator.py): `execute_research_run()`
- [crawler.py](../backend/app/services/crawler.py): `CrawlerProtocol.crawl()`,
  `Crawl4AIAdapter.crawl()`, and `MockCrawl4AICrawler.crawl()`
- [service.py](../scraper/src/service.py): `CompetitorScraper.__init__()` already accepts
  `decision_tracking_context_factory`; no new scraper interface is needed.

**Implementation:**

1. Add an optional keyword-only `research_run_id` argument to the backend crawler
   interface, adapter, mock, and affected test doubles. Existing callers can omit it.
2. In `execute_research_run()`, pass the existing `run_id` into the crawler call.
3. In `Crawl4AIAdapter.crawl()`, construct the scraper with a fresh context per crawl:

   ```python
   from scraper.decision_tracking import DecisionTrackingContext

   scraper = CompetitorScraper(
       decision_tracking_context_factory=lambda _: DecisionTrackingContext(
           research_run_id=str(research_run_id) if research_run_id is not None else None,
       )
   )
   ```

4. Add the serialized `result.decision_tracking` and `result.decision_usage` to the
   returned backend snapshot, using `model_dump(mode="json", exclude_none=True)` when
   present. `save_scraped_data()` already stores that snapshot as JSON.
5. Preserve the complete page list and existing `summary_path`. Save runtime tracking
   references even if disk export fails; do not make a companion file the only record.

**Important:** A disabled tracker returns no reference. Failed/cancelled crawls may have
paid calls without a completed bundle, so support lookup by the supplied research-run
metadata too. Never persist Langfuse credentials. Shared parent traces are optional;
use valid trace IDs, not the numeric backend run ID, for `trace_id`.

**Tests:** Extend [test_crawler.py](../backend/tests/test_crawler.py) and
[test_orchestrator.py](../backend/tests/test_orchestrator.py) to check run-ID forwarding,
separate crawl IDs, disabled tracking, and reference persistence when export fails.

## 2. Include Decisions API costs in backend totals

**Why:** `run_call_names()` currently lists extraction, analysis, and comparison calls
only. The displayed total therefore excludes scraper Decisions calls.

**Files and functions:**

- [costs.py](../backend/app/services/costs.py): `run_call_names()`, `_cost_for_name()`,
  and `fetch_call_costs()`
- [orchestrator.py](../backend/app/services/orchestrator.py): `record_run_cost()` and
  `_record_cost_later()`
- [serializers.py](../backend/app/api/serializers.py): `_cost_usd()`

**Implementation:**

1. Keep the existing analysis lookup. Add a separate Decisions lookup using the saved
   trace/parent reference and `component=scraper_decisions` metadata. The generic names
   `scraper.decisions.candidates` and `scraper.decisions.content` are not unique to a run.
2. Use Observations V2 with usage fields, cursor pagination, and bounded time ranges.
   Count each child generation once, including retry attempts. Do not price the parent
   span or count unrelated observations sharing a trace.
3. Extend `record_run_cost()` to save a Decisions subtotal alongside the existing
   analysis/comparison breakdown. Include it in the overall total. Existing JSON
   storage can hold the breakdown without adding a database column.
4. Extend completeness checks and `_record_cost_later()` retries to cover expected
   Decisions observations. Delayed ingestion, missing usage, missing pricing, or
   truncated retrieval must not produce a complete zero-dollar result.
5. Check `_cost_usd()` and its API tests against the chosen stored total. Preserve
   existing API fields unless the team intentionally adds cost-status fields.

Use the saved Decisions `request_count` to distinguish no dispatched calls from
missing telemetry. Keep absent tracking references distinguishable from measured zero.

**Important cases:** A run can incur Decisions costs even when analysis is skipped
because content is unchanged, or when research later fails. Do not copy a previous
run's analysis costs into the new run. Define comparison attribution explicitly because
one comparison can cover several competitors.

Do not add `decision_usage.estimated_cost_usd` to Langfuse costs for the same calls.
It is a local budget estimate. Ask the Langfuse project owner to verify Decisions model
pricing before relying on dollar totals. Reference creation does not prove ingestion.

**Tests:** Extend [test_costs.py](../backend/tests/test_costs.py) with mocked observations
for retries, pagination, delayed ingestion, unavailable pricing, failed runs, and
unchanged-content runs. Keep pending, unavailable, and measured zero distinguishable.

## 3. Refresh analysis when processing rules change

**Priority:** Recommended so existing competitors benefit from updated analysis rules.

**Why:** `execute_research_run()` reuses previous analysis when page content matches.
Changing deduplication or extraction rules alone does not invalidate that reuse.

**Files and functions:**

- [orchestrator.py](../backend/app/services/orchestrator.py): `execute_research_run()`
- [diff.py](../backend/app/services/diff.py): `compute_content_hash()` and
  `has_content_changed()`
- [repository.py](../backend/app/db/repository.py): `save_analysis_result()`

**Implementation:** Store an `analysis_policy_version` in saved analysis JSON. Reuse
previous findings only when content is unchanged **and** that version matches the
current policy. Treat missing versions as needing reanalysis. Keep the content hash
focused on content; timestamps and tracking IDs must not invalidate it.

Save the policy version on both fresh and reused analysis payloads; the reuse branch
currently constructs a new payload, so it must carry the version forward explicitly.

Bump the version when extraction, prompts, or analysis-input rules materially change.
This comparison must be explicit: adding a top-level version to the crawl snapshot
alone will not affect the current page-based hash. A one-time forced-reanalysis option
is an alternative if the team wants a smaller initial change.

**Tests:** In `test_orchestrator.py`, verify that unchanged content with the same policy
reuses analysis, while a changed/missing policy runs analysis again. Preserve job postings,
role counts, and new-run cost accounting on the reuse path.

## 4. Verify deployment installs the updated scraper and LLM together

**Files:** Root [Dockerfile](../Dockerfile), older [backend/Dockerfile](../backend/Dockerfile),
[backend/README.md](../backend/README.md), and [CI workflow](../.github/workflows/ci.yml).

- Use the root Dockerfile/build context for the research backend. It installs Python
  3.12, `./scraper`, `./llm`, and Chromium. Verify the actual hosting configuration.
- The older backend-only Dockerfile uses Python 3.13 and installs backend requirements
  only. Update or retire that deployment route and its README instructions.
- Rebuild/reinstall after merging. `llm/inputs.py` now imports
  `scraper.content_identity`, which an older installed scraper lacks.
- Preserve the CI install command's `./llm` entry. Both local packages must come from
  the same merged revision. Set shared Langfuse project/region settings on the server.

No database migration or company-analysis schema change is required for the scraper
optimizations themselves. New cost-status API fields, if added, need their own contract review.

## 5. Optional: make the backend crawl budget configurable

**File/function:** [crawler.py](../backend/app/services/crawler.py), `Crawl4AIAdapter.crawl()`.

Replace the hardcoded `max_pages=3` with a validated server setting, keeping a bounded
default. Review the enclosing `execute_research_run()` timeout before increasing it.
More pages can require more time and API usage; do not increase the budget silently.

Coverage guidance is approximate and cannot guarantee all seven topics within three
pages. Fetched duplicates still consume the hard attempt cap. This configuration
improvement is not required for the current workflow to operate.

## Keep the existing handoff intact

- Use `load_analysis_input()` for completed bundles and `deduplicate_analysis_pages()`
  for the in-memory fallback, as already wired in `OpenAIAdapter.analyze()`.
- Retain all original pages in snapshots/exports. Filter only the analysis page list;
  citation indexes refer to that filtered list.
- Treat `crawl_summary.json` as the completion marker. `decision_tracking.json` and
  `crawl_diagnostics.json` are optional companions; coverage hints are not confirmed facts.

See [scraper tracking documentation](../scraper/docs/decision-cost-tracking.md) for the
reference contract and [optimization controls](../scraper/docs/optimizations.md) for flags.
Validate with mocks first, then coordinate one separately authorized end-to-end run.
