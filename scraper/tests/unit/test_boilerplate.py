from lxml import html

from scraper.markdown_cleaning import boilerplate_nodes


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

