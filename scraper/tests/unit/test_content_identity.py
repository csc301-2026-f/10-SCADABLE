import pytest

from scraper.content_identity import markdown_fingerprint
from scraper.extraction import clean_markdown


def test_normalizes_only_controls_line_endings_and_boundary_newlines():
    assert markdown_fingerprint("\nA\r\nB\x00\n") == markdown_fingerprint("A\nB")
    assert clean_markdown("\nA\r\nB\x00\n") == "\nA\nB\n"
    assert markdown_fingerprint(" \t\n") is None


@pytest.mark.parametrize(
    "other",
    [
        "price $50",
        "price €49",
        "Price $49",
        "price  $49",
        " price $49",
        "[price $49](/b)",
        "|price $49|yes|",
    ],
)
def test_preserves_meaningful_markdown_differences(other):
    assert markdown_fingerprint("price $49") != markdown_fingerprint(other)
