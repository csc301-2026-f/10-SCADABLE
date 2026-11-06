# Scraper optimization controls

`ScraperConfig.markdown_remove_boilerplate` defaults to `True`. It removes explicit
OneTrust/Cookiebot consent roots, explicitly labelled cookie dialogs, and flagged
carousel clones with an equivalent original in the same recognized carousel.
Main/article, code and table regions are protected. Uncertain matches remain.
Only known transient root classes and root id/style/aria-hidden/tabindex are
ignored for slide comparison; other root classes and child attributes,
links, labels and meaningful data attributes must match.

Cleanup operates on a separate HTML copy before Markdown generation. Discovery
still sees original HTML and links. Disable it independently of
`markdown_remove_navigation_and_footer` and `markdown_excluded_selectors`.
Disable both automatic flags and leave selectors empty to pass original HTML
through unchanged. Parser failures retain original input.

`ScraperConfig.deduplicate_pages=True` skips pending URLs already observed as
successful, nonempty final fetch targets. It does not infer www, scheme, slash,
query or canonical equivalence. Already dispatched duplicates consume fetch
attempts. Exact repeated Markdown bodies retain their files and discovered links,
but reuse the first assessment and do not update novelty or the evidence ledger.

Identity normalizes controls, line endings and boundary newlines only. It retains
case, indentation, internal whitespace, prices, links and table structure.
`llm.inputs.load_analysis_input(path, deduplicate=False)` independently disables
analysis filtering. The default keeps the first body in source order and retains
the full summary; every referenced file is validated before filtering. Direct
`AnalysisInput` callers can use `deduplicate_analysis_pages` explicitly. Backend
in-memory fallback uses the same helper without changing its snapshot or summary.

Exports include best-effort `crawl_diagnostics.json` version `1.0`, with enabled
settings, skipped redirect aliases and fetched body duplicate mappings. It is
published before the unchanged schema `2.1` completion summary. A sidecar failure
warns and permits the normal export; existing output folders are never replaced.

Coverage diagnostics track seven extraction topics using primary categories and
usefulness >= 2/3 on unique successful pages. Products/services/features indicate
products and services; positioning optimistically indicates target customers and
differentiators; pricing and integrations map directly; news/press releases
indicate commercial activity; careers/hiring indicate hiring. Blog alone does not
indicate commercial activity. Invalid, refused, unavailable or duplicate
assessments add no hints. `indicated` means approximate support, `not_indicated`
means no qualifying category was observed, and `unknown` means assessments were
unavailable or incomplete. None of these establish that a capability is absent.

The native Decisions provider accepts optional `set_coverage_context(CoverageContext)`.
Candidate hints carry only a bounded seven-state vector and the approximation
method, not source snippets. Batching measures the complete request including
hints and instructions. The scoring call remains `score_candidates(candidates)`;
custom providers can omit this optional capability. Concrete relevance and the
configured objective remain primary; gaps guide similarly useful choices only.

`selection.coverage_enabled=True` activates native-provider hints at existing
scoring checkpoints, versioned as `company-research-v3`. Disabling it restores
candidate payloads and usage version `company-research-v2`. Custom providers
without the optional setter retain previous behavior. Structural and BFS ordering
is unchanged. Coverage/link changes share one refresh allowance per candidate;
after two evaluations its last score is retained. Ranking remains 70% relevance,
20% section diversity and 10% novelty, with existing exploration and fetch caps.


## Configuration and integration

All controls are additive strict booleans and support existing global/per-competitor
configuration merging. No dependency, database migration, backend orchestration, or
analysis schema changes are required. Existing in-memory schema `1.1` and export
schema `2.1` remain unchanged. Example opt-outs:

```python
from scraper import ScraperConfig
from llm.inputs import load_analysis_input

settings = ScraperConfig(
    markdown_remove_boilerplate=False,  # consent/clone cleanup only
    deduplicate_pages=False,  # crawler alias and assessment reuse only
    selection={"coverage_enabled": False},
)
analysis = load_analysis_input("bundle/example.com/crawl_summary.json", deduplicate=False)
```

The same keys appear in `config.example.toml`; pass a TOML file through the existing
CLI `--config` option. There are no additional network probes or Decisions questions.
A custom provider can opt into hints with the synchronous method
`set_coverage_context(context: scraper.coverage.CoverageContext)`. Its state is
updated before scoring at existing checkpoints, never during a fetch batch.
Caller-created `AnalysisInput` values are not filtered automatically:

```python
from llm.inputs import deduplicate_analysis_pages
from llm.schemas import AnalysisInput

analysis = AnalysisInput(crawl_summary=summary, pages=deduplicate_analysis_pages(pages))
```

The first representative URL remains available to citation resolution because
analysis citations index the filtered `analysis.pages`, not the complete summary.
The backend's in-memory fallback uses that same rule; its source snapshot and
reconstructed summary retain every source page. Replaying an existing bundle can
change analysis page count under the new default, but creates no Decisions calls.

## Diagnostics contract

`crawl_diagnostics.json` is optional to consumers; require `crawl_summary.json`
before reading a completed bundle. Fields in version `1.0`:

| Field | Meaning |
| --- | --- |
| `features` | Effective boilerplate, crawler deduplication and coverage flags |
| `redirect_aliases` | Skipped normalized URL and the fetched representative URL |
| `body_duplicates` | Retained source URL, first body representative URL and SHA-256 fingerprint |
| `coverage.method` | Category approximation and threshold, including optimistic positioning mapping |
| `coverage.topics` | Seven topic keys with state and supporting URL/category/usefulness records |

Disabled coverage produces unknown topic states. Structural/fallback runs retain
any earlier indications but report unindicated topics as unknown when assessment
is unavailable. Fetched body references also remain in flexible Python page
metadata (`content_fingerprint`, `duplicate_of`); they do not expand the main
summary schema. Diagnostics contain provenance, not copied page bodies.

## Verification and comparison

Automated checks use saved fixtures, fake website/browser results and mocked
OpenAI transports. They establish behavior and compatibility, not improved live
model judgment. In particular, conservative cleanup may leave boilerplate in
protected or unrecognized containers, and exact identity does not merge near
duplicates. Redirect aliases already in flight still consume attempts.

For a separately authorized comparison:

1. Hold inputs, objective, model, page/depth limits, concurrency, exploration and
   Decisions budget fixed. Use fresh output directories and record revisions,
   flags and prompt versions. Website changes can prevent exact comparability.
2. Use the independent flags to isolate cleanup, crawler deduplication and coverage.
   Compare loader filtering separately on the same saved bundle.
3. Inspect Markdown for retained pricing qualifiers, limits, tables, code, links
   and distinct slides. Check duplicates against source files and representative IDs.
4. Manually label factual support for each of the seven topics; do not use primary
   categories as ground truth. Record useful distinct pages per attempt, supported
   citations, actual requests/tokens, uncertain usage, cost estimates and latency.
5. Report losses and trade-offs. Coverage guidance may change selection, and it adds
   a small amount of prompt text. Fewer repeated assessments does not guarantee a
   cheaper or faster complete run. Do not add local estimates to separately tracked
   costs for the same calls.

No live or paid comparison was authorized or run for this implementation.


### Recorded implementation checks

Offline validation on this branch:

- Standalone scraper: **472 passed, 9 opt-in browser tests skipped**.
- Root integration after reinstalling the regular scraper package: **102 passed,
  3 browser tests skipped**.
- Mocked backend LLM adapter: **3 passed**, including duplicate filtering without
  source snapshot or summary loss.
- Scraper lint, changed-file formatting, root loader lint/formatting,
  scraper wheel/source build and `git diff --check` passed.
- Independent read-only review found no important production-code issues. The
  subsequent conservative cleanup correction and combined fixture tests were
  reviewed too; the minor source-preservation test assertion was corrected.

The wider backend orchestration suite could not collect because this environment
has no SQLAlchemy installation and the package is absent from the offline cache.
That check is an environment limitation, not an introduced test assertion failure.
No browser/live/paid comparison was run; improved live selection, evidence coverage,
cost and latency remain unproven.
