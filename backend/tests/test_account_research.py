"""No account secret should be accepted from a public browser or shared across runs."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi import HTTPException

from app.services.account_research import get_account_research_key
from app.services.crawler import Crawl4AIAdapter
from app.services.llm import OpenAIAdapter


ACCOUNT_KEY = "sk-test-account-000000000000000000"
BRIDGE_TOKEN = "some-independent-long-bridge-token"


def test_no_headers_preserve_deployment_key_behavior(monkeypatch):
    monkeypatch.delenv("ACCOUNT_RESEARCH_BRIDGE_TOKEN", raising=False)
    assert get_account_research_key(None, None) is None


def test_account_key_requires_trusted_server_and_valid_token(monkeypatch):
    monkeypatch.setenv("ACCOUNT_RESEARCH_BRIDGE_TOKEN", BRIDGE_TOKEN)
    for key, token, code in [
        (ACCOUNT_KEY, None, 401),
        (ACCOUNT_KEY, "wrong", 401),
        (None, BRIDGE_TOKEN, 422),
        ("invalid", BRIDGE_TOKEN, 422),
        ("sk-short", BRIDGE_TOKEN, 422),
        ("sk-aaaaaaaaaaaaaaa\r", BRIDGE_TOKEN, 422),
    ]:
        with pytest.raises(HTTPException) as error:
            get_account_research_key(key, token)
        assert error.value.status_code == code
        assert ACCOUNT_KEY not in str(error.value.detail)
        assert BRIDGE_TOKEN not in str(error.value.detail)


def test_unconfigured_bridge_rejects_key_not_silent_fallback(monkeypatch):
    monkeypatch.delenv("ACCOUNT_RESEARCH_BRIDGE_TOKEN", raising=False)
    with pytest.raises(HTTPException) as error:
        get_account_research_key(ACCOUNT_KEY, BRIDGE_TOKEN)
    assert error.value.status_code == 503


def test_valid_account_override_is_not_stored_globally(monkeypatch):
    monkeypatch.setenv("ACCOUNT_RESEARCH_BRIDGE_TOKEN", BRIDGE_TOKEN)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-deployment-not-an-account-key")
    assert get_account_research_key(ACCOUNT_KEY, BRIDGE_TOKEN) == ACCOUNT_KEY
    assert os.environ["OPENAI_API_KEY"] == "sk-deployment-not-an-account-key"
    assert OpenAIAdapter(account_openai_key=ACCOUNT_KEY)._account_openai_key == ACCOUNT_KEY
    assert Crawl4AIAdapter(account_openai_key=ACCOUNT_KEY)._account_openai_key == ACCOUNT_KEY
    assert OpenAIAdapter()._account_openai_key is None
    assert Crawl4AIAdapter()._account_openai_key is None


def test_llm_client_settings_use_account_key_only_for_requested_client(monkeypatch):
    from app.services import llm as adapter

    observed = []
    class FakeClient:
        def __init__(self, settings):
            observed.append(settings)

    import sys
    from types import ModuleType
    fake_module = ModuleType("llm.client")
    fake_module.OpenAIClient = FakeClient
    monkeypatch.setitem(sys.modules, "llm.client", fake_module)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-deployment-key-xxxxxxxxxx")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")

    adapter._build_openai_client(ACCOUNT_KEY)
    adapter._build_openai_client()

    assert observed[0].openai_api_key.get_secret_value() == ACCOUNT_KEY
    assert observed[1].openai_api_key.get_secret_value() == "sk-deployment-key-xxxxxxxxxx"
    assert os.environ["OPENAI_API_KEY"] == "sk-deployment-key-xxxxxxxxxx"
    assert ACCOUNT_KEY not in repr(observed[0])


def test_scraper_uses_account_key_not_deployment_key(monkeypatch):
    """The scraper's Decisions SDK factory must receive the request's key."""
    import asyncio
    import sys
    from types import ModuleType, SimpleNamespace
    from app.services import crawler

    keys_used = []
    seen = []

    def add_module(name, **attrs):
        module = ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    class FakeClient:
        def __init__(self, **kwargs):
            keys_used.append(kwargs["api_key"])

    class FakeDecisionProvider:
        def __init__(self, settings, *, client_factory, **kwargs):
            client_factory(api_key="sk-deployment-key-xxxxxxxxxx")
            seen.append(settings.mode)

    class FakeScraper:
        def __init__(self, *, decision_provider_factory, decision_tracking_context_factory):
            self.factory = decision_provider_factory

        async def crawl(self, competitor, config):
            self.factory(competitor, config, None)
            return SimpleNamespace(
                pages=[SimpleNamespace(
                    source_url="https://example.com/features",
                    final_url="https://example.com/features",
                    page_title="Features", page_category="features",
                    cleaned_markdown="Real product features", crawl_status="success",
                )],
                run_status="success", warnings=[],
                homepage_url="https://example.com", decision_tracking=None,
                decision_usage=None,
            )

    add_module("scraper",
               CompetitorInput=lambda **kwargs: SimpleNamespace(**kwargs),
               ScraperConfig=lambda **kwargs: SimpleNamespace(**kwargs),
               SelectionConfig=lambda **kwargs: SimpleNamespace(**kwargs))
    add_module("scraper.environment", load_environment=lambda: None)
    add_module("scraper.output", competitor_directory=lambda _: "example.com",
               export_competitor=lambda *args, **kwargs: None)
    add_module("scraper.service", CompetitorScraper=FakeScraper)
    add_module("scraper.decisions", OpenAIDecisionProvider=FakeDecisionProvider)
    add_module("scraper.decision_tracking", DecisionTrackingContext=lambda **kwargs: None)
    add_module("openai", AsyncOpenAI=FakeClient)

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(crawler, "SessionLocal", FakeDb)
    monkeypatch.setattr(crawler, "get_scraping_page_limit", lambda db: 5)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-deployment-key-xxxxxxxxxx")
    monkeypatch.delenv("ALLOW_MOCK_CRAWLER", raising=False)
    result = asyncio.run(Crawl4AIAdapter(account_openai_key=ACCOUNT_KEY).crawl(
        "Example", "https://example.com"
    ))
    assert result["mode"] == "real"
    assert keys_used == [ACCOUNT_KEY]
    assert seen == ["llm"]
    assert os.environ["OPENAI_API_KEY"] == "sk-deployment-key-xxxxxxxxxx"
