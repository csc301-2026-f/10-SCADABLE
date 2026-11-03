"""Confine SDK import caches to pytest's temporary directory; no live browser tests."""

import pytest


@pytest.fixture(autouse=True)
def sdk_cache_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAWL4_AI_BASE_DIRECTORY", str(tmp_path))
    # Never inherit billable credentials. Adapter tests explicitly install a dummy key.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    # Entry-point tests must not load a developer's real credentials from disk.
    # Dotenv-specific tests explicitly enable loading with temporary fixture files.
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
