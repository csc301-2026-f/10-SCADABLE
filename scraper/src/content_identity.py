"""Exact Markdown identity shared by crawling and analysis."""

import hashlib
import re


def clean_markdown(markdown: str) -> str:
    """Normalize line endings and remove unsafe controls without reflowing Markdown.

    Keep indentation, repeated blank lines, tables, code and two-space hard breaks.
    Filtering is a separate, potentially lossy representation, never the source here.
    """
    return re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", markdown.replace("\r\n", "\n").replace("\r", "\n")
    )


def markdown_fingerprint(markdown: str) -> str | None:
    """Hash meaningful bodies without reflowing or folding content."""
    normalized = clean_markdown(markdown).strip("\n")
    if not normalized.strip():
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
