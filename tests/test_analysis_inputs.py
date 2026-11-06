"""Read real scraper bundles without cleaning or discarding their provenance."""

import json

import pytest
from pydantic import ValidationError

from llm.inputs import load_analysis_input
from tests.helpers import MARKDOWN, NOW, make_page, make_result, write_export


def test_loader_preserves_markdown_and_redirect_metadata(tmp_path):
    page = make_page(
        source_url="https://example.com/products/atlas?currency=cad",
        final_url="https://example.com/products/atlas/",
        page_title="Atlas",
        selection={
            "method": "ranked",
            "relevance_score": 0.9,
            "assessment_available": True,
        },
    )
    index = write_export(tmp_path, make_result(pages=[page]))

    data = load_analysis_input(index)

    assert data.company_name == "Example"
    assert data.pages[0].markdown == MARKDOWN
    source = data.pages[0].source
    assert str(source.url) == "https://example.com/products/atlas?currency=cad"
    assert str(source.final_url) == "https://example.com/products/atlas/"
    assert source.title == "Atlas" and source.category == "features"
    assert source.crawled_at == NOW
    assert source.selection.method == "ranked"
    assert data.crawl_summary.schema_version == "2.1"


def test_partial_export_keeps_failures_and_warnings_outside_prompt_pages(tmp_path):
    failed = make_page(
        source_url="https://example.com/broken",
        crawl_status="failed",
        error_message="HTTP 503",
    )
    index = write_export(
        tmp_path,
        make_result(
            pages=[make_page(), failed], run_status="partial", warnings=["Limited run"]
        ),
    )

    data = load_analysis_input(index)

    assert len(data.pages) == 1
    assert data.crawl_summary.status == "partial"
    assert data.crawl_summary.counts.failed == 1
    assert data.crawl_summary.warnings == ["Limited run"]


@pytest.mark.parametrize(
    "homepage", ["http://example.com/", "https://example.com:8443/"]
)
def test_loader_follows_explicit_index_for_nondefault_origins(tmp_path, homepage):
    result = make_result(
        homepage_url=homepage, pages=[make_page(homepage_url=homepage)]
    )
    index = write_export(tmp_path, result)

    assert index.parent.name.startswith("example.com--")
    assert str(load_analysis_input(index).crawl_summary.homepage_url) == homepage


@pytest.mark.parametrize("kind", ["failed", "empty", "whitespace"])
def test_loader_rejects_exports_without_usable_markdown(tmp_path, kind):
    if kind == "failed":
        page = make_page(crawl_status="failed", error_message="Fetch failed")
        result = make_result(
            pages=[page], run_status="failed", completion_reason="failed"
        )
    else:
        result = make_result(pages=[] if kind == "empty" else [make_page()])
    index = write_export(tmp_path, result)
    if kind == "whitespace":
        (index.parent / "pages/index.md").write_text(" \n\t", encoding="utf-8")

    with pytest.raises(ValueError, match="No usable Markdown") as caught:
        load_analysis_input(index)
    if kind == "failed":
        assert "Fetch failed" in str(caught.value)


@pytest.mark.parametrize("corruption", ["version", "path", "counts"])
def test_loader_validates_summary_with_scraper_contract(tmp_path, corruption):
    index = write_export(tmp_path)
    payload = json.loads(index.read_text(encoding="utf-8"))
    if corruption == "version":
        payload["schema_version"] = "99.0"
    elif corruption == "path":
        payload["pages"][0]["markdown_file"] = "pages/../../outside.md"
    else:
        payload["counts"]["successful"] = 99
    index.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_analysis_input(index)


def test_loader_requires_completion_marker_and_referenced_files(tmp_path):
    index = write_export(tmp_path)
    (index.parent / "pages/index.md").unlink()
    with pytest.raises(FileNotFoundError):
        load_analysis_input(index)
    index.unlink()
    with pytest.raises(FileNotFoundError):
        load_analysis_input(index)


def test_loader_preserves_utf8_and_line_endings(tmp_path):
    index = write_export(tmp_path)
    markdown = "  # Caf\u00e9\r\n\r\nLine break.  \r\n"
    (index.parent / "pages/index.md").write_bytes(markdown.encode("utf-8"))

    assert load_analysis_input(index).pages[0].markdown == markdown


def test_loader_skips_blank_page_but_retains_its_metadata(tmp_path):
    index = write_export(
        tmp_path,
        make_result(
            pages=[make_page(), make_page(source_url="https://example.com/pricing")]
        ),
    )
    (index.parent / "pages/index.md").write_text(" \n\t", encoding="utf-8")

    data = load_analysis_input(index)

    assert len(data.pages) == 1
    assert str(data.pages[0].source.url) == "https://example.com/pricing"
    assert data.crawl_summary.counts.successful == 2


def test_loader_rejects_a_reference_resolving_outside_bundle(tmp_path):
    index = write_export(tmp_path / "run")
    outside = tmp_path / "outside.md"
    outside.write_text("Outside the bundle", encoding="utf-8")
    content = index.parent / "pages/index.md"
    content.unlink()
    try:
        content.symlink_to(outside)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip(
                "Creating symlinks requires Windows Developer Mode or symlink privileges"
            )
        raise

    with pytest.raises(ValueError, match="escapes the export bundle"):
        load_analysis_input(index)


def test_unusable_export_error_keeps_competitor_and_startup_diagnostics(tmp_path):
    index = write_export(
        tmp_path,
        make_result(
            run_status="failed",
            completion_reason="failed",
            pages=[],
            warnings=[
                "Chromium missing; install chromium using the application interpreter"
            ],
        ),
    )
    with pytest.raises(ValueError) as caught:
        load_analysis_input(index)
    message = str(caught.value)
    assert "https://example.com/" in message
    assert "failed" in message
    assert "Chromium missing" in message
    assert str(index) in message


def test_exact_duplicates_only_filter_analysis_pages_and_opt_out_restores_them(
    tmp_path,
):
    index = write_export(
        tmp_path,
        make_result(
            pages=[
                make_page(),
                make_page(source_url="https://example.com/copy"),
                make_page(
                    source_url="https://example.com/distinct",
                    cleaned_markdown="Different price $50",
                ),
            ]
        ),
    )
    data = load_analysis_input(index)
    assert len(data.pages) == 2
    assert str(data.pages[0].source.url) == "https://example.com/"
    assert len(data.crawl_summary.pages) == data.crawl_summary.counts.successful == 3
    assert len(load_analysis_input(index, deduplicate=False).pages) == 3
    assert (index.parent / "pages/copy.md").exists()
    (index.parent / "pages/copy.md").unlink()
    with pytest.raises(FileNotFoundError):
        load_analysis_input(index)
