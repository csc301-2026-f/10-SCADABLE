from lxml import html

from scraper.markdown_cleaning import boilerplate_nodes


def test_generator_wires_independent_boilerplate_opt_out():
    from scraper import ScraperConfig
    from scraper.crawl4ai_adapter import build_run_config

    source = '<div id="onetrust-banner-sdk">CONSENT_UI</div><p>Product evidence</p>'
    for enabled in (True, False):
        generator = build_run_config(
            ScraperConfig(
                markdown_remove_boilerplate=enabled,
                markdown_remove_navigation_and_footer=False,
            )
        ).markdown_generator
        result = generator.generate_markdown(source)
        assert ("CONSENT_UI" in result.raw_markdown) is not enabled
        assert "Product evidence" in result.raw_markdown
    assert "CONSENT_UI" in source


def removed(source):
    document = html.document_fromstring(source)
    return {node.get("id") for node in boilerplate_nodes(document)}


def test_only_explicit_consent_ui_is_identified():
    assert removed("""<body><div id="onetrust-banner-sdk">Consent</div>
        <div id="preferences" role="dialog" aria-label="Cookie preferences">Settings</div>
        <div id="ordinary" role="dialog">Cookies are our product</div>
        <p id="prose">Cookie settings API documentation</p>
        <main><div id="CookiebotWidget">Product example</div></main>
        <table><tr><td id="onetrust-consent-sdk">Example</td></tr></table></body>""") == {
        "onetrust-banner-sdk",
        "preferences",
    }


def test_clones_require_equivalent_original_in_same_carousel():
    assert removed("""<div class="slick-slider">
        <div id="original" class="slick-slide" data-product="a"><a href="/a">$49</a></div>
        <div id="clone" class="slick-slide slick-cloned" aria-hidden="true"
          style="width: 20px" tabindex="-1" data-product="a"><a href="/a">$49</a></div>
        <div id="unique" class="slick-cloned" data-product="a"><a href="/b">$49</a></div>
        <div id="child-diff" class="slick-cloned" data-product="a">
          <a href="/a" aria-label="Annual">$49</a></div>
        <div id="data-diff" class="slick-cloned" data-product="b"><a href="/a">$49</a></div>
        </div><div class="slick-slider"><div id="orphan" class="slick-cloned"
          data-product="a"><a href="/a">$49</a></div></div>""") == {"clone"}


def test_unique_hidden_slides_and_protected_regions_are_retained():
    assert not removed("""<div class="swiper"><div id="one">Original</div>
        <div id="two" class="swiper-slide-duplicate" hidden>Different limitation</div></div>
        <article><div id="onetrust-banner-sdk">Article example</div></article>
        <pre><code id="CookiebotWidget">example</code></pre>""")


def test_meaningful_root_classes_and_protected_descendants_prevent_removal():
    assert not removed("""<div class="slick-slider">
        <div class="slick-slide annual"><p>$49</p></div>
        <div id="monthly" class="slick-slide slick-cloned monthly"><p>$49</p></div>
        </div><div id="onetrust-banner-sdk"><article>Important evidence</article></div>""")
