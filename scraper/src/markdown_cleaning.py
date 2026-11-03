"""Prepare a separate HTML copy for Markdown, without modifying discovery evidence."""

import logging
from collections.abc import Sequence
from copy import deepcopy

from cssselect import ExpressionError
from lxml import etree, html
from lxml.cssselect import CSSSelector, LxmlHTMLTranslator

_CONTENT_CONTAINERS = {"main", "article", "section", "aside"}
logger = logging.getLogger(__name__)

_CONSENT_IDS = {
    "onetrust-consent-sdk",
    "onetrust-banner-sdk",
    "onetrust-pc-sdk",
    "CybotCookiebotDialog",
    "CookiebotWidget",
}
_COOKIE_LABELS = {"cookie consent", "cookie preferences", "cookie settings"}
_CLONE_CLASSES = {"slick-cloned", "swiper-slide-duplicate"}
_CAROUSEL_CLASSES = {"slick-slider", "swiper", "swiper-container"}
_PROTECTED = {"main", "article", "pre", "code", "table"}


def _protected(node) -> bool:
    return any(
        item.tag in _PROTECTED or item.get("role", "").lower() == "main"
        for item in (node, *node.iterancestors())
    )


def _carousel(node):
    return next(
        (
            ancestor
            for ancestor in node.iterancestors()
            if ancestor.get("aria-roledescription", "").lower() == "carousel"
            or set(ancestor.get("class", "").split()) & _CAROUSEL_CLASSES
        ),
        None,
    )


def _slide_identity(node):
    """Ignore only presentation/lifecycle attributes on the slide root."""
    slide = deepcopy(node)
    for attribute in ("id", "class", "aria-hidden", "tabindex", "style"):
        slide.attrib.pop(attribute, None)

    # Structural comparison keeps child attributes, text, links and meaningful data.
    def identity(item):
        return (
            item.tag,
            tuple(sorted(item.attrib.items())),
            item.text,
            tuple((identity(child), child.tail) for child in item),
        )

    return identity(slide)


def boilerplate_nodes(document) -> set:
    """Identify only explicit consent UI and corroborated carousel clones."""
    matches = set()
    for node in document.iter():
        if not isinstance(node.tag, str) or _protected(node):
            continue
        label = node.get("aria-label", "")
        if not label and node.get("aria-labelledby"):
            ids = node.get("aria-labelledby").split()
            label = " ".join(
                item.text_content() for item in document.iter() if item.get("id") in ids
            )
        if node.get("id") in _CONSENT_IDS or (
            (node.tag == "dialog" or node.get("role", "").lower() == "dialog")
            and " ".join(label.lower().split()) in _COOKIE_LABELS
        ):
            matches.add(node)
            continue
        if not set(node.get("class", "").split()) & _CLONE_CLASSES:
            continue
        carousel = _carousel(node)
        if carousel is None:
            continue
        signature = _slide_identity(node)
        if any(
            other is not node
            and _carousel(other) is carousel
            and not set(other.get("class", "").split()) & _CLONE_CLASSES
            and _slide_identity(other) == signature
            for other in carousel.iterdescendants()
            if isinstance(other.tag, str)
        ):
            matches.add(node)
    return matches


class _HtmlSelectorTranslator(LxmlHTMLTranslator):
    """HTML parsing has no named XML namespaces; reject them during translation."""

    def xpath_element(self, selector):
        if selector.namespace not in {None, "", "*"}:
            raise ExpressionError("Named CSS namespaces are not supported for HTML cleanup")
        return super().xpath_element(selector)

    def xpath_attrib(self, selector):
        if selector.namespace not in {None, "", "*"}:
            raise ExpressionError("Named CSS namespaces are not supported for HTML cleanup")
        return super().xpath_attrib(selector)


def compile_markdown_selector(selector: str) -> CSSSelector:
    return CSSSelector(selector, translator=_HtmlSelectorTranslator())


def _is_site_chrome(node) -> bool:
    if not isinstance(node.tag, str) or node.tag in {"html", "body"}:
        return False
    roles = set(node.get("role", "").lower().split())
    if node.tag == "nav" or "navigation" in roles:
        return True
    # Page/article introductions and attribution are content, not site chrome.
    scoped = node.tag in _CONTENT_CONTAINERS or any(
        ancestor.tag in _CONTENT_CONTAINERS or "main" in ancestor.get("role", "").lower().split()
        for ancestor in node.iterancestors()
    )
    return not scoped and (node.tag == "footer" or bool(roles & {"banner", "contentinfo"}))


def prepare_markdown_html(
    source: str,
    *,
    remove_navigation_and_footer: bool = True,
    excluded_selectors: Sequence[str] = (),
) -> str:
    """Remove semantic site chrome and explicit CSS matches, preserving adjacent text.

    Never searches for menu/footer words in prose or guesses CSS class names. A
    document that cannot be parsed falls back to its original input.
    """
    if not source.strip() or (not remove_navigation_and_footer and not excluded_selectors):
        return source
    parser = html.HTMLParser(encoding="utf-8", no_network=True, recover=True)
    try:
        document = html.document_fromstring(
            source.encode("utf-8"),
            parser=parser,
        )
    except (etree.ParserError, etree.XMLSyntaxError):
        return source
    # Recovery can return a truncated tree without raising on parser resource limits.
    if any(error.level >= etree.ErrorLevels.FATAL for error in parser.error_log):
        logger.warning("Markdown cleanup skipped after fatal HTML parser recovery; source retained")
        return source
    matches = set()
    if remove_navigation_and_footer:
        matches.update(node for node in document.iter() if _is_site_chrome(node))
    for selector in excluded_selectors:
        matches.update(compile_markdown_selector(selector)(document))
    if document in matches:
        return ""
    # Skip children of already-excluded containers; drop_tree retains each outer tail.
    for node in matches:
        if node.getparent() is not None and not any(
            ancestor in matches for ancestor in node.iterancestors()
        ):
            node.drop_tree()
    return html.tostring(document, encoding="unicode", method="html")
