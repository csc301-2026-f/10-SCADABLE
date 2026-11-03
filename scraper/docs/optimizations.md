# Scraper optimization controls

`ScraperConfig.markdown_remove_boilerplate` defaults to `True`. It removes explicit
OneTrust/Cookiebot consent roots, explicitly labelled cookie dialogs, and flagged
carousel clones with an equivalent original in the same recognized carousel.
Main/article, code and table regions are protected. Uncertain matches remain.
Root presentation attributes are ignored for slide comparison; child attributes,
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
