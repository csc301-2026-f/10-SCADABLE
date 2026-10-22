"""Exercise SDK structured parsing against an in-memory HTTP transport."""

import asyncio
import json

import httpx2
import pytest
from openai import AsyncOpenAI

import llm.client as client_module
from llm.config import Settings
from llm.schemas import CompanyAnalysis, ExtractedInformation, SWOTAnalysis


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_openai_requires_nonempty_key(api_key):
    settings = Settings(openai_api_key=api_key, openai_model="test", _env_file=None)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        client_module.OpenAIClient(settings)


def test_openai_requires_model():
    settings = Settings(openai_api_key="test", openai_model="", _env_file=None)
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        client_module.OpenAIClient(settings)


@pytest.mark.parametrize("schema", [ExtractedInformation, CompanyAnalysis])
def test_output_urls_use_supported_json_schema_strings(schema):
    items = schema.model_json_schema()["$defs"]["ClaimCited"]["properties"][
        "source_url"
    ]
    assert items["type"] == "string"
    assert "format" not in items


@pytest.mark.parametrize("has_output", [True, False])
@pytest.mark.parametrize("has_usage", [True, False])
def test_responses_structured_output(monkeypatch, has_output, has_usage):
    expected = SWOTAnalysis(
        strengths=["Example evidence"],
        weaknesses=[],
        opportunities=[],
        threats=[],
        competitor_notes=[],
    )
    requests = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        output = []
        if has_output:
            output = [
                {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": expected.model_dump_json(),
                            "annotations": [],
                        }
                    ],
                }
            ]
        return httpx2.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "test-model",
                "output": output,
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
                "usage": {
                    "input_tokens": 1000,
                    "input_tokens_details": {
                        "cached_tokens": 200,
                        "cache_write_tokens": 300,
                    },
                    "output_tokens": 400,
                    "output_tokens_details": {"reasoning_tokens": 100},
                    "total_tokens": 1400,
                }
                if has_usage
                else None,
            },
        )

    def offline_sdk(**kwargs) -> AsyncOpenAI:
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        )

    monkeypatch.setattr(client_module, "AsyncOpenAI", offline_sdk)

    async def run() -> None:
        client = client_module.OpenAIClient(
            Settings(
                openai_api_key="offline-test-key",
                openai_model="test-model",
                _env_file=None,
            )
        )
        try:
            if has_output:
                result = await client.generate_structured(
                    instructions="Test instructions",
                    input_text="Test facts",
                    output_schema=SWOTAnalysis,
                )
                assert result == expected
            else:
                with pytest.raises(ValueError, match="no parsed structured output"):
                    await client.generate_structured(
                        instructions="Test instructions",
                        input_text="Test facts",
                        output_schema=SWOTAnalysis,
                    )
        finally:
            await client.close()
        assert client.sdk.is_closed()
        assert client.usage.request_count == 1
        assert len(client.usage.responses) == int(has_usage)
        if has_usage:
            record = client.usage.responses[0]
            assert record.model == "test-model"
            assert (record.input_tokens, record.cached_input_tokens) == (1000, 200)
            assert (record.cache_write_tokens, record.output_tokens) == (300, 400)
        else:
            assert client.usage.estimated_cost_usd() is None

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/responses"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "test-model"
    assert payload["text"]["format"]["type"] == "json_schema"


def test_read_timeout_keeps_usage_unknown_and_closes_client(monkeypatch):
    from openai import APITimeoutError

    observed_timeouts = []

    def respond(request):
        observed_timeouts.append(request.extensions["timeout"]["read"])
        raise httpx2.ReadTimeout("offline simulated slow response", request=request)

    def offline_sdk(**kwargs):
        return AsyncOpenAI(
            **kwargs,
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        )

    monkeypatch.setattr(client_module, "AsyncOpenAI", offline_sdk)

    async def run():
        client = client_module.OpenAIClient(
            Settings(
                openai_api_key="offline-test-key",
                openai_model="test-model",
                openai_timeout_seconds=75.0,
                _env_file=None,
            )
        )
        try:
            with pytest.raises(APITimeoutError):
                await client.generate_structured(
                    instructions="Test",
                    input_text="Test",
                    output_schema=SWOTAnalysis,
                )
        finally:
            await client.close()
        assert client.sdk.is_closed()
        assert client.usage.request_count == 1
        assert client.usage.estimated_cost_usd() is None

    asyncio.run(run())
    assert observed_timeouts == [75.0]
