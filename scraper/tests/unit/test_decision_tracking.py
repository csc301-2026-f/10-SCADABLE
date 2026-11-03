"""Optional telemetry must never affect Decisions or expose their payloads."""

import sys
from types import SimpleNamespace

import pytest

from scraper import decision_tracking as tracking


class FakeObservation:
    def __init__(self, options, number):
        self.options = options
        self.trace_id = options.get("trace_context", {}).get("trace_id", "a" * 32)
        self.id = f"{number:016x}"
        self.updates = []
        self.ended = False

    def update(self, **values):
        self.updates.append(values)

    def end(self):
        self.ended = True


class FakeLangfuse:
    def __init__(self):
        self.observations = []
        self.flush_count = 0

    def start_observation(self, **options):
        observation = FakeObservation(options, len(self.observations) + 1)
        self.observations.append(observation)
        return observation

    def flush(self):
        self.flush_count += 1


@pytest.fixture
def telemetry(monkeypatch):
    client = FakeLangfuse()
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return client

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", " public-test ")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", " secret-test ")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://telemetry.example")
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=factory))
    return client, options


@pytest.mark.parametrize("mode,public,secret", [
    ("structural", "public", "secret"),
    ("llm", "", "secret"),
    ("llm", "public", "   "),
])
def test_disabled_tracking_does_not_initialize_sdk(monkeypatch, telemetry, mode, public, secret):
    client, options = telemetry
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", public)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", secret)
    recorder = tracking.DecisionTracker(mode=mode, model="test")
    recorder.start()
    assert recorder.reference is None
    assert not client.observations and not options


def test_missing_extra_warns_once_without_exception_details(monkeypatch, telemetry, caplog):
    monkeypatch.setitem(sys.modules, "langfuse", None)
    recorder = tracking.DecisionTracker(mode="llm", model="test")
    recorder.start()
    assert recorder.reference is None
    assert "scraper[langfuse]" in caplog.text
    assert len(caplog.records) == 1
    assert "secret-test" not in caplog.text


def test_initialization_failure_is_optional_and_sanitized(monkeypatch, telemetry, caplog):
    def fail(**kwargs):
        raise RuntimeError("secret-server-response")

    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=fail))
    recorder = tracking.DecisionTracker(mode="llm", model="test")
    recorder.start()
    assert recorder.reference is None
    assert "secret-server-response" not in caplog.text
    assert len(caplog.records) == 1


def test_parent_and_generation_use_explicit_context(telemetry):
    client, options = telemetry
    context = tracking.DecisionTrackingContext(
        research_run_id="42", trace_id="b" * 32, parent_observation_id="c" * 16
    )
    recorder = tracking.DecisionTracker(mode="llm", model="test", context=context)
    recorder.start()
    observation = recorder.start_attempt(operation="candidates", attempt=2, sequence=3)
    recorder.finish_attempt(observation, outcome="success", input_tokens=0)
    parent, generation = client.observations
    assert options == [{
        "public_key": "public-test", "secret_key": "secret-test",
        "base_url": "https://telemetry.example",
    }]
    assert parent.options["trace_context"] == {
        "trace_id": "b" * 32, "parent_span_id": "c" * 16,
    }
    assert generation.options["trace_context"] == {
        "trace_id": parent.trace_id, "parent_span_id": parent.id,
    }
    assert generation.options["name"] == "scraper.decisions.candidates"
    assert generation.options["metadata"]["research_run_id"] == "42"
    assert generation.options["metadata"]["request_sequence"] == 3
    assert generation.updates[-1]["usage_details"] == {"input": 0}
    assert generation.ended
    assert recorder.reference.crawl_id == context.crawl_id
    assert recorder.reference.trace_id == parent.trace_id
    assert tracking.DecisionTrackingContext().crawl_id != context.crawl_id
