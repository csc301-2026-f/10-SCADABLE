from pathlib import Path

import pytest
from lxml import html
from pydantic import ValidationError

from scraper import ScraperConfig, resolve_config
from scraper.crawl4ai_adapter import build_run_config

SOURCE = (Path(__file__).parents[1] / "fixtures/site_chrome.html").read_text(encoding="utf-8")


def generate(**settings):
    generator = build_run_config(ScraperConfig(**settings)).markdown_generator
    return generator.generate_markdown(SOURCE, base_url="https://example.com/")


def test_removes_site_chrome_before_both_markdown_representations():
    result = generate()
    for markdown in (result.raw_markdown, result.fit_markdown or ""):
        for removed in (
            "GLOBAL_BANNER",
            "GLOBAL_NAV",
            "NESTED_NAV",
            "ARIA_NAV",
            "GLOBAL_FOOTER",
            "ARIA_FOOTER",
            "/nav-only",
            "/footer-only",
        ):
            assert removed not in markdown
    assert "Company evidence" in result.raw_markdown
    assert "Important introduction after navigation." in result.raw_markdown
    assert "Important closing text after footer." in result.raw_markdown
    assert "CUSTOM_MENU" in result.raw_markdown  # No class-name guessing.


def test_preserves_article_headers_footers_body_links_tables_and_code():
    markdown = generate().raw_markdown
    for retained in (
        "Page introduction worth preserving.",
        "Article introduction.",
        "Article author: Jane Example.",
        "Section attribution.",
        "Main content attribution.",
        "Customer result: four audit days.",
        "$49",
        "[Product details](https://example.com/details)",
        "code example",
    ):
        assert retained in markdown


def test_css_exclusions_are_applied_only_to_markdown_copy():
    from scraper.markdown_cleaning import prepare_markdown_html

    document = prepare_markdown_html(SOURCE, excluded_selectors=(".custom-menu", ".custom-footer"))
    assert "CUSTOM_MENU" not in document and "CUSTOM_FOOTER" not in document
    original = html.document_fromstring(SOURCE)
    assert original.xpath('//a[@href="/nav-only"]')
    assert "GLOBAL_NAV" in SOURCE and "CUSTOM_MENU" in SOURCE
    assert "Product details" in document


def test_configured_css_exclusions_reach_sdk_generator():
    markdown = generate(markdown_excluded_selectors=[".custom-menu", ".custom-footer"]).raw_markdown
    assert "CUSTOM_MENU" not in markdown and "CUSTOM_FOOTER" not in markdown


def test_default_cleanup_can_be_disabled_without_disabling_explicit_selectors():
    markdown = generate(
        markdown_remove_navigation_and_footer=False, markdown_excluded_selectors=[".custom-menu"]
    ).raw_markdown
    assert "GLOBAL_NAV" in markdown and "GLOBAL_FOOTER" in markdown
    assert "CUSTOM_MENU" not in markdown


def test_disabling_all_cleanup_passes_original_input_through_unchanged():
    from scraper.markdown_cleaning import prepare_markdown_html

    assert (
        prepare_markdown_html(SOURCE, remove_navigation_and_footer=False, remove_boilerplate=False)
        == SOURCE
    )


def test_global_and_competitor_cleanup_overrides_merge_normally():
    effective = resolve_config(
        ScraperConfig(markdown_excluded_selectors=[".global-menu"]),
        ScraperConfig(
            markdown_remove_navigation_and_footer=False, markdown_excluded_selectors=[".local-menu"]
        ),
    )
    assert effective.markdown_remove_navigation_and_footer is False
    assert effective.markdown_excluded_selectors == [".local-menu"]


@pytest.mark.parametrize(
    "selector",
    ["", "div[", "a::text", "svg|g", "[svg|href]", "section [svg|href]", "section:not(svg|g)"],
)
def test_invalid_css_is_rejected_before_browser_startup(selector):
    with pytest.raises(ValidationError):
        ScraperConfig(markdown_excluded_selectors=[selector])


def test_fatal_parser_recovery_preserves_deep_and_following_content():
    from scraper.markdown_cleaning import prepare_markdown_html

    source = (
        "<html><body><nav>Menu</nav>"
        + "<div>" * 300
        + "<p>DEEP_EVIDENCE</p>"
        + "</div>" * 300
        + "<p>AFTER_EVIDENCE</p></body></html>"
    )
    prepared = prepare_markdown_html(source)
    assert "DEEP_EVIDENCE" in prepared and "AFTER_EVIDENCE" in prepared
    markdown = build_run_config(ScraperConfig()).markdown_generator.generate_markdown(source)
    assert "DEEP_EVIDENCE" in markdown.raw_markdown
    assert "AFTER_EVIDENCE" in markdown.raw_markdown


def test_html_selectors_support_case_insensitive_tags_and_literal_pipes():
    from scraper.markdown_cleaning import prepare_markdown_html

    settings = ScraperConfig(markdown_excluded_selectors=["DIV[lang|='en']"])
    cleaned = prepare_markdown_html(
        '<main>Company evidence</main><div lang="en-CA">Custom menu</div>',
        excluded_selectors=settings.markdown_excluded_selectors,
    )
    assert "Company evidence" in cleaned and "Custom menu" not in cleaned


@pytest.mark.parametrize(
    "source", ["", "  ", "<nav>Menu</nav><main><p>Company facts", "Just company text"]
)
def test_empty_fragment_and_incomplete_html_are_handled(source):
    from scraper.markdown_cleaning import prepare_markdown_html

    cleaned = prepare_markdown_html(source)
    if "Company facts" in source:
        assert "Company facts" in cleaned and "Menu" not in cleaned
    elif "Just company text" in source:
        assert "Just company text" in cleaned
    else:
        assert not cleaned.strip()
