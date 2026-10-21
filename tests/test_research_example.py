"""The example exposes both invocation modes without credentials at startup."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/research_pipeline.py"


@pytest.mark.parametrize(
    "args,code,expected",
    [
        (["--help"], 0, "--export"),
        ([], 2, "Provide homepage URLs or --export"),
        (["https://example.com/", "--export", "unused.json"], 2, "exclusively"),
        (["https://example.com/", "--llm-rates", "bad", "0", "0", "0"], 2, "rate"),
        (["https://example.com/", "--llm-rates", "NaN", "0", "0", "0"], 2, "rate"),
        (["https://example.com/", "--llm-rates", "-1", "0", "0", "0"], 2, "rate"),
    ],
)
def test_example_invocation_modes_without_api_requests(args, code, expected):
    environment = dict(os.environ, PYTHON_DOTENV_DISABLED="1")
    environment.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-B", str(EXAMPLE), *args],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == code, result.stderr
    assert expected in result.stdout + result.stderr


@pytest.mark.parametrize("replay", [True, False])
@pytest.mark.parametrize("company_count", [1, 2])
def test_cost_summary_keeps_stdout_json_and_replay_does_not_charge_old_crawl(
    monkeypatch, capsys, replay, company_count
):
    from examples import research_pipeline as example
    from llm.usage import LLMUsage, UsageRecord

    client = SimpleNamespace(
        usage=LLMUsage(
            request_count=2,
            responses=[
                UsageRecord("gpt-6-luna", 1000, 200, 300, 400),
                UsageRecord("gpt-6-luna", 500, 100, 0, 200),
            ],
        )
    )
    summary = SimpleNamespace(decision_usage=SimpleNamespace(estimated_cost_usd=0.001))
    result = SimpleNamespace(
        crawl_summary=summary, model_dump=lambda **kw: {"ok": True}
    )

    async def run(args, owned_client):
        assert owned_client is client
        return [result] * company_count

    monkeypatch.setattr(example, "load_environment", lambda *a, **kw: None)
    monkeypatch.setattr(example, "OpenAIClient", lambda settings: client)
    monkeypatch.setattr(example, "_run", run)
    args = ["--export", "unused.json"] if replay else ["https://example.com/"]
    assert example.main(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [{"ok": True}] * company_count
    decisions = 0 if replay else 0.001 * company_count
    assert f"Decisions API: ${decisions:.6f}" in captured.err
    assert "LLM analysis: $0.000431" in captured.err
    assert f"Total: ${decisions + 0.000431:.6f}" in captured.err


def test_custom_rates_are_used_by_the_example(monkeypatch, capsys):
    from examples import research_pipeline as example
    from llm.usage import LLMUsage, UsageRecord

    client = SimpleNamespace(
        usage=LLMUsage(
            request_count=1,
            responses=[
                UsageRecord("custom-model", 1000, 200, 300, 400),
            ],
        )
    )

    async def run(args, owned_client):
        return []

    monkeypatch.setattr(example, "load_environment", lambda *a, **kw: None)
    monkeypatch.setattr(example, "OpenAIClient", lambda settings: client)
    monkeypatch.setattr(example, "_run", run)
    assert (
        example.main(
            ["--export", "unused.json", "--llm-rates", "1", "0.5", "2", "1.25"]
        )
        == 0
    )
    assert "LLM analysis: $0.001775" in capsys.readouterr().err


def test_failed_run_reports_unknown_cost_instead_of_zero(monkeypatch, capsys):
    from examples import research_pipeline as example
    from llm.usage import LLMUsage

    client = SimpleNamespace(usage=LLMUsage(request_count=1))

    async def fail(args, owned_client):
        raise ValueError("Analysis failed")

    monkeypatch.setattr(example, "load_environment", lambda *a, **kw: None)
    monkeypatch.setattr(example, "OpenAIClient", lambda settings: client)
    monkeypatch.setattr(example, "_run", fail)
    with pytest.raises(SystemExit) as error:
        example.main(["https://example.com/"])
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert "Analysis failed" in captured.err
    assert "Decisions API: unavailable" in captured.err
    assert "LLM analysis: unavailable" in captured.err
    assert "Total: unavailable" in captured.err
