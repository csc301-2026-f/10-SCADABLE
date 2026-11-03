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
