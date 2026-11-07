"""Exercise SDK structured parsing against an in-memory HTTP transport."""

import asyncio
import json

import httpx2
import pytest
from openai import AsyncOpenAI

import llm.client as client_module
from llm.config import Settings
from llm.schemas import (
    PartialExtractedInformationByIndex,
    SWOTAnalysisByIndex,
)


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_openai_requires_nonempty_key(api_key):
    settings = Settings(openai_api_key=api_key, openai_model="test", _env_file=None)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        client_module.OpenAIClient(settings)


def test_openai_requires_model():
    settings = Settings(openai_api_key="test", openai_model="", _env_file=None)
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        client_module.OpenAIClient(settings)


@pytest.mark.parametrize(
    ("schema", "citation_schema", "index_field"),
    [
        (PartialExtractedInformationByIndex, "ClaimCitedByIndex", "claim_page_index"),
        (SWOTAnalysisByIndex, "SWOTIndex", "swot_component_page_index"),
    ],
)
def test_output_page_citations_use_plain_integer_json_schema(
    schema, citation_schema, index_field
):
    prop = schema.model_json_schema()["$defs"][citation_schema]["properties"][index_field]
    assert prop["type"] == "integer"
    assert "format" not in prop


@pytest.mark.parametrize("has_output", [True, False])
def test_responses_structured_output(monkeypatch, has_output):
    expected = SWOTAnalysisByIndex(competitor_notes=[], SWOT_entries=[], scores=[])
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
                    output_schema=SWOTAnalysisByIndex,
                )
                assert result == expected
            else:
                with pytest.raises(ValueError, match="no parsed structured output"):
                    await client.generate_structured(
                        instructions="Test instructions",
                        input_text="Test facts",
                        output_schema=SWOTAnalysisByIndex,
                    )
        finally:
            await client.close()
        assert client.sdk.is_closed()

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/responses"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "test-model"
    assert payload["text"]["format"]["type"] == "json_schema"
