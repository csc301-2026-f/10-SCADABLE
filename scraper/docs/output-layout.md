# Markdown exports and crawl summaries

The CLI writes a content directory per competitor. Each successful page gets one UTF-8
Markdown file containing exactly its `cleaned_markdown`; the summary contains references
and essential metadata. No front matter, JSON metadata or synthetic headings are added
to page content. Failed pages do not create Markdown files.

```text
output/run-001/
  competitor-a.example/
    crawl_summary.json
    decisions.jsonl          # only when tracing is enabled
    crawl_diagnostics.json  # best effort duplicate and approximate coverage diagnostics
    pages/
      index.md
      pricing.md
      products/
        analytics.md
  competitor-b.example/
    crawl_summary.json
    pages/
      index.md
```

## Filename rules

| Requested URL | Relative content path |
| --- | --- |
| `/` | `pages/index.md` |
| `/pricing` | `pages/pricing.md` |
| `/pricing/` | `pages/pricing/index.md` |
| `/products/analytics` | `pages/products/analytics.md` |
| `/pricing?currency=cad` | `pages/pricing--q-<url-hash>.md` |

Files are named from the requested URL's existing normalized identity. Original source
URLs remain in the index; `final_url` is included when redirected. The exporter reuses
the established normalizer for fragment/tracking removal and meaningful query identity;
it does not change discovery or fetch URLs.

Path segments are decoded for readable names, sanitized and bounded. Lossy/long segments
and Windows device names receive a stable hash; device basenames are prefixed before
any extension. Distinct query variants, case-insensitive collisions and literal `index`
paths are disambiguated. Allocation sorts URL identities, so discovery completion order
does not affect filenames. Exact duplicate logical page outcomes are rejected rather
than silently replacing content. The crawler already prevents those duplicate outcomes.

Directory components never end in `.md`, avoiding conflicts with content files. Secondary
source hosts use `pages/_hosts/<host-directory>/`; this includes approved extra hosts
and apex/www source variants. Very long hierarchies use a readable hashed filename under
`pages/_long/`. Relative paths are bounded to keep Windows paths practical, but callers
should still choose a reasonably short output root.

The default HTTPS competitor directory is its hostname, including `www` when supplied.
Other schemes/ports receive a stable origin suffix. Distinct origins remain separate.
The summary's relative paths always use `/`, on both Windows and POSIX.

## Summary schema and relationships

`CrawlExportSummary` is schema `2.1`. It contains competitor identity, UTC start/finish
timestamps, total duration, run status/completion reason, counts, attempted `pages` and
`uncrawled_urls`. Warnings and `discovery_truncated` appear when relevant. Empty pending
counts and unavailable optional metadata are omitted.
It also carries compact `decision_usage` when supplied by the provider and
`pages[].selection` diagnostics when available. See [decisions.md](decisions.md) for
model/prompt version, reported versus uncertain input tokens, cost estimate and fallback
events. `decision_trace_file` appears only for opt-in trace export.

A page entry corresponds to its Markdown file, for example:

```json
{
  "url": "https://competitor-a.example/pricing",
  "url_path": "/pricing",
  "markdown_file": "pages/pricing.md",
  "title": "Pricing",
  "category": "pricing",
  "word_count": 7,
  "crawled_at": "2026-10-16T18:00:01Z",
  "duration_seconds": 1.2,
  "status": "success",
  "depth": 1,
  "depth_basis": "link",
  "discovery_sources": ["internal_link"],
  "discovered_from": ["https://competitor-a.example/"],
  "selection": {
    "method": "ranked",
    "relevance_score": 0.9,
    "assessment_available": true
  }
}
```

Its file contains only the source content:

```markdown
# Pricing

Starter: $49/month.

Pro: $99/month.
```

`word_count` counts Unicode word tokens in the Markdown, including link/code text; it is
an approximate size measure. Page duration measures the service fetch call, including
capacity waits, retries and backoff, and excludes subsequent extraction/export. Older or
custom fetch results without that measurement omit it. Total crawl duration is derived
from the result's start/finish timestamps and excludes export I/O.

Failed entries have `status="failed"`, a compact error and no Markdown reference. HTTP
status, multiple-attempt count, retry stop reason and redirected URL are included where
useful. Code-context/stack-heavy errors are compacted to 500 characters. Full diagnostics,
all Markdown representations and effective configuration remain in the Python result.

Each attempted or uncrawled URL has its recorded `depth`, `depth_basis`, unique discovery
methods and parent URLs. Parents can be page URLs or sitemap documents. A sitemap seed's
depth is logical, not a verified homepage link distance. Multiple observations are folded
into one entry rather than duplicating a crawl graph. The crawler stores at most eight
distinct evidence records per URL, so these relationships describe recorded provenance,
not an exhaustive link graph. Existing discovery caps remain visible through warnings
and truncation. No extra request is made to reconstruct missing relationships.

Counts are derived from the exported attempted/uncrawled entries and validated on load.
Uncrawled entries retain their skipped/deferred/external/pending status and reason.
Examples include `page_limit`, `depth_limit` or `domain_not_approved`; exact reasons are
provided by discovery. Pages absent from a custom result's registry can still be indexed,
with unknown discovery fields omitted rather than invented. An entry marked crawled or
failed without its corresponding page outcome is rejected as inconsistent input.

## API, CLI and publication

```python
from scraper.output import export_competitor

# result is a completed CompetitorCrawlResult from the unchanged async service.
# summary = export_competitor(result, "output/run-001")
```

The exporter performs synchronous local file I/O and returns a typed `CrawlExportSummary`.
The CLI calls it as each competitor completes. `--output` selects a directory, default
`output/`. Existing competitor folders are refused before CLI crawling and checked again
at publication. Use `--output output/run-002` for a repeat run. Other competitors and
unrelated files in the root can coexist and are not changed.

All content is staged in a temporary directory under the same root. An exclusive mkdir
reserves the final competitor directory; pages and optional `decisions.jsonl` move first
and best-effort `crawl_diagnostics.json` publishes before `crawl_summary.json` moves last.
Diagnostics failures warn without preventing the ordinary export. Its version `1.0`
payload is documented in [optimization controls](optimizations.md). The summary is the completion marker. A write/staging
error cleans the owned temporary directory. An interruption/publication failure can leave an incomplete folder
without a summary; a hard process kill can leave a hidden staging folder. Consumers
should require the index before accepting an export and use a fresh root for retries.
This is not a directory-wide atomic transaction or a power-loss durability guarantee.

The old JSON/JSONL writers and record models were removed, along with `--format`,
`--overwrite` and stdout-export CLI options. Existing generated data is not deleted or
automatically converted. The Python `CompetitorCrawlResult` retains full
in-memory content with additive schema `1.1` diagnostics. See [integration.md](integration.md)
for validated index loading and reading referenced files without a backend or database.
