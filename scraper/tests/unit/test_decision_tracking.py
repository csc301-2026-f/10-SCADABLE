"""Optional telemetry must never affect Decisions or expose their payloads."""

import asyncio
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from openai import APIConnectionError, APIStatusError

from scraper import decision_tracking as tracking
from scraper.config import SelectionConfig
from scraper.decisions import DecisionAccessError, OpenAIDecisionProvider


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


@pytest.mark.parametrize(
    "mode,public,secret",
    [
        ("structural", "public", "secret"),
        ("llm", "", "secret"),
        ("llm", "public", "   "),
    ],
)
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


def test_disabled_sdk_does_not_expose_nonrecording_reference(monkeypatch, telemetry):
    original = telemetry[0].start_observation

    def noop(**options):
        observation = original(**options)
        observation.id = "0" * 16
        observation.trace_id = "0" * 32
        return observation

    monkeypatch.setattr(telemetry[0], "start_observation", noop)
    recorder = tracking.DecisionTracker(mode="llm", model="test")
    recorder.start()
    assert recorder.reference is None


@pytest.mark.parametrize(
    "context",
    [
        tracking.DecisionTrackingContext(trace_id="invalid"),
        tracking.DecisionTrackingContext(trace_id="0" * 32),
        tracking.DecisionTrackingContext(parent_observation_id="a" * 16),
    ],
)
def test_invalid_parent_context_disables_tracking_instead_of_misattributing(telemetry, context):
    recorder = tracking.DecisionTracker(mode="llm", model="test", context=context)
    recorder.start()
    assert recorder.reference is None
    assert telemetry[0].observations == []


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
    assert options == [
        {
            "public_key": "public-test",
            "secret_key": "secret-test",
            "base_url": "https://telemetry.example",
        }
    ]
    assert parent.options["trace_context"] == {
        "trace_id": "b" * 32,
        "parent_span_id": "c" * 16,
    }
    assert generation.options["trace_context"] == {
        "trace_id": parent.trace_id,
        "parent_span_id": parent.id,
    }
    assert generation.options["name"] == "scraper.decisions.candidates"
    assert generation.options["metadata"]["research_run_id"] == "42"
    assert generation.options["metadata"]["request_sequence"] == 3
    assert generation.updates[-1]["usage_details"] == {"input": 0}
    assert generation.ended
    assert recorder.reference.crawl_id == context.crawl_id
    assert recorder.reference.trace_id == parent.trace_id
    assert tracking.DecisionTrackingContext().crawl_id != context.crawl_id


PAYLOAD = {"input": "private-page-excerpt", "questions": [{"name": "q", "type": "score"}]}
ANSWER = {"name": "q", "type": "score", "score": 2.0, "confidence": 0.9}


def sdk(replies):
    return SimpleNamespace(
        decisions=SimpleNamespace(create=AsyncMock(side_effect=replies)), close=AsyncMock()
    )


@pytest.mark.parametrize("operation", ["candidates", "content"])
@pytest.mark.parametrize("tokens", [0, 123])
async def test_provider_records_only_reported_usage_and_metadata(
    monkeypatch, telemetry, operation, tokens
):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    client, _ = telemetry
    openai = sdk([{"answers": [ANSWER], "usage": {"input_tokens": tokens}}])
    async with OpenAIDecisionProvider(
        SelectionConfig(), client_factory=lambda **_: openai
    ) as provider:
        result = await provider._request(PAYLOAD, operation)
    assert result["q"]["type"] == "score"
    parent, generation = client.observations
    assert generation.updates[-1]["usage_details"] == {"input": tokens}
    assert generation.updates[-1]["status_message"] == "success"
    assert generation.options["metadata"]["request_sequence"] == 1
    assert parent.ended and generation.ended
    assert "private-page-excerpt" not in repr(client.observations[1].options)
    assert "output" not in generation.updates[-1]
    openai.close.assert_awaited_once()


@pytest.mark.parametrize(
    "body,tokens",
    [
        ({"answers": "invalid", "usage": {"input_tokens": 12}}, 12),
        ({"answers": [{}], "usage": {"input_tokens": 12}}, 12),
        ({"answers": [ANSWER]}, None),
        ({"answers": [ANSWER], "usage": {"input_tokens": -1}}, None),
        ({"answers": [ANSWER], "usage": {"input_tokens": True}}, None),
    ],
)
async def test_invalid_response_preserves_known_usage(monkeypatch, telemetry, body, tokens):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    openai = sdk([body])
    async with OpenAIDecisionProvider(
        SelectionConfig(), client_factory=lambda **_: openai
    ) as provider:
        await provider._request(PAYLOAD, "content")
    values = telemetry[0].observations[-1].updates[-1]
    assert values["status_message"] == "invalid_response"
    assert values.get("usage_details") == ({"input": tokens} if tokens is not None else None)


async def test_retry_records_each_dispatch_without_changing_budget(monkeypatch, telemetry):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    error = APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com"))
    openai = sdk([error, {"answers": [ANSWER], "usage": {"input_tokens": 12}}])
    async with OpenAIDecisionProvider(
        SelectionConfig(), client_factory=lambda **_: openai, sleep=AsyncMock()
    ) as provider:
        await provider._request(PAYLOAD, "candidates")
    first, second = telemetry[0].observations[1:]
    assert first.updates[-1]["status_message"] == "api_error"
    assert "usage_details" not in first.updates[-1]
    assert [o.options["metadata"]["attempt"] for o in (first, second)] == [1, 2]
    assert [o.options["metadata"]["request_sequence"] for o in (first, second)] == [1, 2]
    assert provider.summary.request_count == 2
    assert provider.summary.reported_input_tokens == 12
    assert provider.summary.uncertain_input_tokens > 0


@pytest.mark.parametrize("kind", ["auth", "timeout", "cancelled"])
async def test_failed_attempts_end_and_do_not_invent_usage(monkeypatch, telemetry, kind):
    import asyncio

    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    if kind == "auth":
        response = httpx2.Response(401, request=httpx2.Request("POST", "https://api.openai.com"))
        error = APIStatusError("sensitive-body", response=response, body=None)
        expected = DecisionAccessError
    else:
        error = asyncio.CancelledError() if kind == "cancelled" else TimeoutError()
        expected = asyncio.CancelledError if kind == "cancelled" else None
    openai = sdk([error])
    async with OpenAIDecisionProvider(
        SelectionConfig(max_retries=0), client_factory=lambda **_: openai
    ) as provider:
        if expected:
            with pytest.raises(expected):
                await provider._request(PAYLOAD, "content")
        else:
            assert await provider._request(PAYLOAD, "content") is None
    generation = telemetry[0].observations[-1]
    assert generation.ended
    assert (
        generation.updates[-1]["status_message"]
        == {
            "auth": "http_401",
            "timeout": "timeout",
            "cancelled": "cancelled",
        }[kind]
    )
    assert "usage_details" not in generation.updates[-1]


@pytest.mark.parametrize("budget,payload", [(0.0, PAYLOAD), (0.03, {"input": "x" * 50000})])
async def test_undispatched_requests_have_no_generations(monkeypatch, telemetry, budget, payload):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    openai = sdk([])
    async with OpenAIDecisionProvider(
        SelectionConfig(budget_usd=budget), client_factory=lambda **_: openai
    ) as provider:
        assert await provider._request(payload, "candidates") is None
    assert len(telemetry[0].observations) == 1
    openai.decisions.create.assert_not_awaited()


async def test_service_propagates_context_and_separates_concurrent_crawls(monkeypatch, telemetry):
    from scraper import CompetitorInput, FetchedPage, ScraperConfig, decisions
    from scraper.service import CompetitorScraper
    from scraper.types import utc_now

    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    original = decisions.OpenAIDecisionProvider

    async def answer(**payload):
        return {
            "answers": [
                {
                    "name": q["name"],
                    "type": q["type"],
                    "confidence": 0.9,
                    **({"score": 2.0} if q["type"] == "score" else {"choice": "features"}),
                }
                for q in payload["questions"]
            ],
            "usage": {"input_tokens": 10},
        }

    clients = []

    def provider(settings, **kwargs):
        client = sdk([])
        client.decisions.create = AsyncMock(side_effect=answer)
        clients.append(client)
        return original(settings, client_factory=lambda **_: client, **kwargs)

    class Fetcher:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def fetch(self, url, settings):
            return FetchedPage(
                source_url=url,
                crawl_timestamp=utc_now(),
                crawl_status="success",
                raw_markdown="# Features\nConcrete evidence.",
            )

    contexts = {}

    def context_for(competitor):
        context = tracking.DecisionTrackingContext(research_run_id=competitor.display_name)
        contexts[competitor.display_name] = context
        return context

    monkeypatch.setattr(decisions, "OpenAIDecisionProvider", provider)
    service = CompetitorScraper(
        fetcher_factory=lambda *_: Fetcher(),
        http_transport=httpx.MockTransport(
            lambda req: (
                httpx.Response(200, text="User-agent: *\nAllow: /\n")
                if req.url.path == "/robots.txt"
                else httpx.Response(404)
            )
        ),
        decision_tracking_context_factory=context_for,
    )
    settings = ScraperConfig(max_pages=1, request_delay_seconds=(0.0, 0.0))
    results = [
        r
        async for r in service.crawl_many(
            [
                CompetitorInput(homepage_url="https://one.example"),
                CompetitorInput(homepage_url="https://two.example"),
            ],
            settings,
        )
    ]
    assert len(contexts) == 2
    assert len({r.decision_tracking.crawl_id for r in results}) == 2
    assert len({r.decision_tracking.trace_id for r in results}) == 2
    for result in results:
        reference = result.decision_tracking
        assert reference.crawl_id == contexts[result.competitor_name].crawl_id
        assert reference.research_run_id == result.competitor_name
        assert reference.finished_at >= reference.started_at
        assert "decision_tracking" not in result.model_dump()
        assert "decision_tracking" not in result.model_json_schema()["properties"]
        assert result.summary.successful == 1
    assert all(c.close.await_count == 1 for c in clients)


async def test_exit_flushes_once_off_event_loop_without_shutting_down(monkeypatch, telemetry):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    client, _ = telemetry
    threads = []
    original = client.flush

    def flush():
        threads.append(threading.get_ident())
        original()

    monkeypatch.setattr(client, "flush", flush)
    openai = sdk([])
    async with OpenAIDecisionProvider(
        SelectionConfig(), client_factory=lambda **_: openai
    ) as provider:
        pass
    assert client.flush_count == 1
    assert threads[0] != threading.get_ident()
    assert client.observations[0].ended
    assert provider.decision_tracking.finished_at is not None
    await provider.__aexit__(None, None, None)
    assert client.flush_count == 1


async def test_slow_flush_has_bounded_wait(monkeypatch, telemetry, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    monkeypatch.setattr(tracking, "FLUSH_TIMEOUT_SECONDS", 0.01)
    client, _ = telemetry
    release = threading.Event()
    monkeypatch.setattr(client, "flush", lambda: release.wait(1))
    openai = sdk([])
    try:
        async with asyncio.timeout(0.5):
            async with OpenAIDecisionProvider(SelectionConfig(), client_factory=lambda **_: openai):
                pass
        assert "flush" in caplog.text
        openai.close.assert_awaited_once()
    finally:
        release.set()


@pytest.mark.parametrize("failure", ["disabled", "start", "update", "end", "flush"])
async def test_telemetry_failure_does_not_change_results_or_budgets(
    monkeypatch, telemetry, failure, caplog
):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    client, _ = telemetry

    async def run():
        openai = sdk([{"answers": [ANSWER], "usage": {"input_tokens": 12}}])
        async with OpenAIDecisionProvider(
            SelectionConfig(), client_factory=lambda **_: openai
        ) as provider:
            result = await provider._request(PAYLOAD, "candidates")
        openai.close.assert_awaited_once()
        return result, provider.summary.model_dump()

    baseline = await run()

    def fail(*args, **kwargs):
        raise RuntimeError("sensitive-telemetry-details")

    if failure == "disabled":
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY")
    elif failure in ("start", "flush"):
        monkeypatch.setattr(client, "start_observation" if failure == "start" else "flush", fail)
    else:
        monkeypatch.setattr(FakeObservation, failure, fail)
    assert await run() == baseline
    assert "sensitive-telemetry-details" not in caplog.text


async def test_task_cancellation_closes_all_resources(monkeypatch, telemetry):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    dispatched = asyncio.Event()

    async def wait(**_):
        dispatched.set()
        await asyncio.Event().wait()

    openai = sdk([])
    openai.decisions.create = AsyncMock(side_effect=wait)

    provider = OpenAIDecisionProvider(SelectionConfig(), client_factory=lambda **_: openai)

    async def run():
        async with provider:
            await provider._request(PAYLOAD, "content")

    task = asyncio.create_task(run())
    await dispatched.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(o.ended for o in telemetry[0].observations)
    assert telemetry[0].flush_count == 1
    assert telemetry[0].observations[-1].updates[-1]["status_message"] == "cancelled"
    openai.close.assert_awaited_once()


async def test_cancellation_waiting_for_capacity_has_no_generation(monkeypatch, telemetry):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    openai = sdk([])
    async with OpenAIDecisionProvider(
        SelectionConfig(), client_factory=lambda **_: openai, request_limiter=asyncio.Semaphore(0)
    ) as provider:
        task = asyncio.create_task(provider._request(PAYLOAD, "content"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(telemetry[0].observations) == 1
    assert provider.summary.request_count == 0


async def test_real_langfuse_sdk_exports_usage_and_parentage_in_memory(monkeypatch):
    from uuid import uuid4

    langfuse = pytest.importorskip("langfuse")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", f"pk-lf-{uuid4().hex}")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-offline")
    exporter = InMemorySpanExporter()
    tracer = TracerProvider()
    real_factory = langfuse.Langfuse
    clients = []

    def factory(**options):
        client = real_factory(**options, tracer_provider=tracer, span_exporter=exporter)
        clients.append(client)
        return client

    def reject_http(*args, **kwargs):
        raise AssertionError("External HTTP is forbidden in this test")

    monkeypatch.setattr(httpx.Client, "send", reject_http)
    monkeypatch.setattr(langfuse, "Langfuse", factory)
    openai = sdk([{"answers": [ANSWER], "usage": {"input_tokens": 12}}])
    try:
        async with OpenAIDecisionProvider(
            SelectionConfig(), client_factory=lambda **_: openai
        ) as provider:
            await provider._request(PAYLOAD, "candidates")
        spans = {s.name: s for s in exporter.get_finished_spans()}
        parent = spans["scraper.decisions"]
        generation = spans["scraper.decisions.candidates"]
        assert generation.parent.span_id == parent.context.span_id
        assert generation.context.trace_id == parent.context.trace_id
        assert f"{parent.context.trace_id:032x}" == provider.decision_tracking.trace_id
        assert generation.attributes["langfuse.observation.usage_details"] == '{"input": 12}'
        assert not any(k.endswith(("input", "output")) for k in generation.attributes)
    finally:
        for client in clients:
            client.shutdown()


async def test_flush_failure_preserves_original_access_error(monkeypatch, telemetry):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    response = httpx2.Response(403, request=httpx2.Request("POST", "https://api.openai.com"))
    openai = sdk([APIStatusError("private-body", response=response, body=None)])

    def fail():
        raise RuntimeError("telemetry-failure")

    monkeypatch.setattr(telemetry[0], "flush", fail)
    with pytest.raises(DecisionAccessError, match="HTTP 403"):
        async with OpenAIDecisionProvider(
            SelectionConfig(), client_factory=lambda **_: openai
        ) as provider:
            await provider._request(PAYLOAD, "content")
    openai.close.assert_awaited_once()
