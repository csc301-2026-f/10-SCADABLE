# Rendering, extraction and output

## Data flow and component API

`CompetitorScraper.crawl()` returns one `CompetitorCrawlResult`. `crawl_many()` is an
async iterator yielding complete competitor results in completion order. Each run uses
the existing discovery frontier and keeps its fetched pages for extraction without
another request. A browser belongs to one competitor; HTTP clients, browsers and owned
pages close on normal exit or cancellation. Service objects do not own an event loop.

```python
from contextlib import aclosing
from scraper import CompetitorInput, ScraperConfig
from scraper.service import CompetitorScraper


async def collect_or_stop(homepages, consume):
    service = CompetitorScraper()
    competitors = [CompetitorInput(homepage_url=url) for url in homepages]
    async with aclosing(service.crawl_many(competitors, ScraperConfig())) as results:
        async for result in results:
            if not await consume(result):
                break  # aclosing cancels remaining workers and releases their browsers
```

Models, interfaces and the service can be imported without loading the SDK. The browser
starts on entering the fetcher's async context. An injected `fetcher_factory(competitor,
settings, robots_guard)` supports backend tests without browsers. An optional HTTPX
transport replaces sitemap/robots HTTP at its network boundary for offline fixtures.

`crawl_many()` shares global/CLI page and hostname concurrency ceilings across competitors.
Effective per-competitor settings still describe that competitor's discovery limits;
shared ceilings apply in addition. Page starts on a shared hostname respect configured
spacing and robots minimum intervals. The default browser adapter reserves a hostname
slot for every actual render hop, including redirect destinations, and shares document
start pacing across competitors. Custom fetcher factories receive source-host limits
from the service and own their internal redirect policy; a custom Crawl4AI adapter can
receive a shared `HostLimiter` explicitly. The service also shares that limiter with
robots and sitemap HTTP; the HTTP request budget remains separate from page reservations.
Rendering resources are not counted as independently crawled pages.

## Browser and dynamic-page policy

Crawl4AI 0.9.5 supplies Chromium rendering, HTML/metadata/link extraction and Markdown
generation. The public page-context hook installs document routing before navigation.
The existing exact-host/Protego guard is the robots authority; the adapter disables
the redundant SDK robots lookup and uses the same explicit user agent everywhere.

Each main document request uses Playwright `route.fetch(max_redirects=0, max_retries=0)`.
For a redirect, the adapter aborts the current browser navigation and starts the next
SDK navigation only after checking the destination. It never sends the redirect response
to the browser, which would otherwise let automatic redirect hops bypass routing.
The final page is rendered at its real URL, retaining normal relative-link resolution.
All redirect hops share one attempt deadline and distinct-page reservation. Redirect
history is retained in metadata. An SDK navigation abort is expected during this process.

Third-party scripts, styles, images and fetch/XHR requests are allowed for rendering.
Embedded documents, popups, non-GET documents and unexpected document navigation are
blocked; iframe content is not collected. Service-worker bypass is enabled per page.
An ordinary link is discovered rather than clicked. JavaScript-only controls that replace
the entire document are not supported as load-more buttons; use page links/configuration
appropriate to that site. Public HTML pages are supported; PDF extraction is outside scope.

The first matching `dynamic_rules` URL glob wins. Its explicitly configured fields override
`global.dynamic`, retaining omitted global fields. Rules are reevaluated after redirects.
CSS waits, SDK scrolling and configured button clicks have finite limits. A missing,
hidden or disabled load-more button ends pagination normally; interaction errors fail
that page. `scroll_delay_seconds` also supplies the pause after each click; sites loading
asynchronously may need a longer pause. Content already present in the DOM is extracted;
there is no claim that arbitrary infinite scrolling or virtualized lists are exhausted.

```toml
[[competitors.overrides.dynamic_rules]]
url_pattern = "https://competitor.example/careers*"
wait_for_css = "main.jobs"
scan_full_page = true
max_scroll_steps = 5
load_more_selector = "button.load-more"
max_clicks = 3
scroll_delay_seconds = 0.5
```

Pagination URLs found in links enter the shared frontier and obey its normal page/depth
budgets. Same-document load-more interactions count toward the current page's finite
click/time limits. Source content and links come from the final rendered DOM snapshot.

## Content and categorization

- `raw_markdown`: SDK Markdown generated from a separate copy of rendered `raw_html`,
  retaining content text links. Navigation and site footers are removed from that copy;
  images are omitted during conversion (`ignore_images=True`). These exclusions apply
  to filtered/cleaned content and Markdown excerpts sent to Decisions too.
  It is readable source Markdown, not byte-for-byte HTML or a script archive.
- `filtered_markdown`: SDK `PruningContentFilterLXML` output (threshold 0.2, minimum word
  threshold 0). This representation can omit useful material and is never substituted
  for the source or conservative cleaned version.
- `cleaned_markdown`: full Markdown with normalized line endings and unsafe control
  characters removed. Indentation, hard breaks, blank lines, tables and code are preserved.

### Navigation and footer removal

`ContentMarkdownGenerator` extends the SDK's public generation hook. It parses an HTML
copy with lxml's network-disabled parser and delegates Markdown conversion and the
existing optional pruning filter to Crawl4AI. The original rendered HTML, SDK link list
and contextual discovery evidence remain intact. Nothing is removed from the browser DOM.

Default `markdown_remove_navigation_and_footer=true` removes `<nav>`, ARIA navigation,
and site-level `<footer>`, `banner` and `contentinfo` elements. Footers/banner-like elements
inside `main`, `article`, `section` or `aside` are retained conservatively. Ordinary
headers remain, including page/article titles and introductions. Adjacent text outside
removed elements is preserved. CSS class names and words in prose are not guessed.

For generic `<div>` menus or footers, supply explicit CSS selectors in
`markdown_excluded_selectors`, globally or per competitor. Invalid selectors fail
configuration validation before browser startup. Explicit selectors apply even inside
content containers, so choose the actual menu/footer containers rather than broad tags.
HTML selectors use case-insensitive tag names; named XML namespace selectors are not
supported (use plain HTML selectors such as `svg g` instead).
Lists replace across override layers, consistent with existing settings.

Set `markdown_remove_navigation_and_footer=false` to disable navigation/footer removal.
`markdown_remove_boilerplate=true` independently removes explicit consent UI and confirmed
carousel clones, preserving protected content and uncertain matches. See
[optimization controls](optimizations.md). Set both automatic flags to false and use an
empty selector list to disable all cleanup. Explicit selectors still apply otherwise.
Empty/whitespace input passes through, and HTML that cannot be parsed falls back to the original input.
Fatal recovery errors, such as excessive nesting, also retain the source and log a warning
instead of silently truncating content. Boilerplate may remain on those exceptional pages.
An exclusion that removes all readable content can produce the existing empty-content
failure instead of reintroducing excluded content. No main-only extraction or new
pruning policy is enabled. Exact-body deduplication reuses assessments without deleting
fetched Markdown files. `filtered_markdown` remains optional and
is not substituted for the exported source-based Markdown.

Old output folders are not rewritten. Run again into a fresh directory to see cleanup;
custom menus not covered by semantics need site-specific selectors. No extra model
requests or candidate-page probes are introduced.

`MarkdownExtractor` requires no network access. Categories now consume the content-based
Decisions assessment attached during discovery, or use `other` when unavailable, including
structural mode. No URL/title keyword classifier remains. One category is a coarse source
label, not a claim that a mixed page contains only that kind of information. See
[adaptive selection](decisions.md) for the model boundary and API-free alternative.

SDK metadata and response headers are JSON-normalized and preserved. Adapter metadata
adds redirect history, load-more click count and up to 20 blocked embedded document URLs.
Failed SDK results can retain partial content. HTTP errors and empty Markdown are failures.
Extraction failures are reconciled with URL dispositions so result counts stay consistent.

## Markdown bundles and summary indexes

The current CLI exports one directory per competitor. Every successful page has a
content-only Markdown file containing its exact `cleaned_markdown`. Failed pages have
an index entry with an error and no content file. The schema `2.1` `CrawlExportSummary`
contains relative file references, counts, timestamps, durations and compact discovery
relationships; it does not embed page bodies or raw SDK metadata.
It adds per-page selection diagnostics and competitor usage/fallback accounting.
Opt-in `decisions.jsonl` contains bounded request evidence and typed answers and is staged
with the Markdown before the summary completion marker. See [migration.md](migration.md)
for strict-reader changes from schema `2.0`.

Use `export_competitor(result, output_directory)` to export a completed typed result.
The exporter stages files, reserves a new competitor directory and publishes
`crawl_summary.json` last. Consumers should require that completion marker. Completed
competitors survive later interruption. Existing competitor directories are refused;
choose a fresh output directory for another run. Old generated JSON/JSONL files are
not deleted. The old file writers/record models and CLI format/overwrite/stdout options
have been removed.

The in-memory `CompetitorCrawlResult` still preserves full/filtered/cleaned Markdown and
SDK metadata for direct Python consumers. See [output-layout.md](output-layout.md) for
path rules, minimal schemas, bounded provenance and interruption details, and
[integration.md](integration.md) for backend/LLM consumption.

## Verification and current limits

```powershell
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
uv run --locked scraper --help
```

Opt-in Chromium verification uses only local fixture servers:

```powershell
uv run --locked python -m playwright install chromium
$env:SCRAPER_BROWSER_TESTS = "1"
uv run --locked pytest tests/integration/test_browser_local.py -q
```

If browser binaries are stored in a custom directory, set `PLAYWRIGHT_BROWSERS_PATH` for
both installation and execution. During development they were stored in the ignored
`.uv-cache/playwright` directory. The cross-host loopback-script fixture grants a local
network permission in its test-only subclass; production does not grant it.

The service implements bounded transient retries and records actual `attempt_count` plus
per-attempt diagnostics. Capacity and rate waits have a separate deadline from rendering.
See [integration.md](integration.md) for reliability, failure classifications and consumer
examples.

Official references: [Markdown generation](https://docs.crawl4ai.com/core/markdown-generation/),
[browser hooks](https://docs.crawl4ai.com/advanced/hooks-auth/),
[page interactions](https://docs.crawl4ai.com/core/page-interaction/),
[Playwright route fetching](https://playwright.dev/python/docs/api/class-route#route-fetch).
