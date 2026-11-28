"""Reviewer retries: fixable output is corrected, broken output fails cleanly."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from llm import analyzer as analyzer_module
from llm.analyzer import Analyzer
from llm.inputs import load_analysis_input
from llm.schemas import (
    ExtractionFeedback,
    ExtractionFeedbacks,
    SWOTAnalysisByIndex,
    SWOTEntryByIndex,
    SWOTFeedback,
    SWOTFeedbacks,
    SWOTIndex,
)
from tests.test_analyzer import one_page_export, partial_facts
from tests.test_scores import DATA

pytestmark = pytest.mark.asyncio


class _FakeSpan:
    def update(self, **fields):
        pass


class ThreadedFakeClient:
    """The OpenAIClient thread API on top of a fake's generate_structured(**request).

    Kept in this file so these retry tests do not depend on the shared helpers.
    """

    async def start_thread(self, *, instructions, metadata=None):
        threads = self.__dict__.setdefault("_threads", {})
        conversation_id = f"thread-{len(threads)}"
        threads[conversation_id] = instructions
        return conversation_id

    @contextmanager
    def create_session(self, *, name, session_id):
        self.__dict__.setdefault("sessions", []).append({"name": name, "session_id": session_id})
        yield _FakeSpan()

    async def generate_in_thread(self, *, session, conversation_id, input_text, output_schema,
                                 observation_name="", pre_status="", post_status=""):
        return await self.generate_structured(
            instructions=self._threads[conversation_id],
            input_text=input_text,
            output_schema=output_schema,
            observation_name=observation_name,
        )

    async def generate_response(self, *, input_text, output_schema, observation_name,
                                instructions=None, conversation_id=None, session=None):
        if output_schema in (ExtractionFeedbacks, SWOTFeedbacks):
            return SimpleNamespace(output_parsed=output_schema(feedbacks=[]))
        parsed = await self.generate_structured(
            instructions=instructions,
            input_text=input_text,
            output_schema=output_schema,
            observation_name=observation_name,
        )
        return SimpleNamespace(output_parsed=parsed)


class ScriptedClient(ThreadedFakeClient):
    """Replies with each task answer in turn and each reviewer verdict in turn."""

    def __init__(self, answers, verdicts=()):
        self.answers = list(answers)
        self.verdicts = list(verdicts)
        self.requests = []
        self.reviews = []

    async def generate_in_thread(self, *, session, **request):
        assert session is not None, "every task call must run inside the session"
        return await super().generate_in_thread(session=session, **request)

    async def generate_structured(self, **request):
        self.requests.append(request)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    async def generate_response(self, *, output_schema, **request):
        if output_schema in (ExtractionFeedbacks, SWOTFeedbacks):
            self.reviews.append(request["input_text"])
            verdict = self.verdicts.pop(0) if self.verdicts else output_schema(feedbacks=[])
            return SimpleNamespace(output_parsed=verdict)
        return await super().generate_response(output_schema=output_schema, **request)


def swot(page_index):
    """A valid SWOT for DATA when page_index is 0, a broken citation otherwise."""
    return SWOTAnalysisByIndex(
        competitor_notes=[],
        SWOT_entries=[SWOTEntryByIndex(
            component="threat", what="Vanta covers more frameworks",
            swot_citations=[SWOTIndex(swot_component_company_index=1, swot_component_page_index=page_index)],
        )],
        scores=[],
    )


async def test_extraction_retries_flagged_field_and_keeps_the_rest(tmp_path):
    first = partial_facts()
    fixed = first.model_copy(update={"summary": "Corrected summary"})
    client = ScriptedClient(
        [first, fixed],
        verdicts=[ExtractionFeedbacks(feedbacks=[ExtractionFeedback(what="summary", why="Too vague")])],
    )
    analyzer = Analyzer(client, trace_prefix="run-7:example", session_id="run-7")

    result = await analyzer.extract_information(load_analysis_input(one_page_export(tmp_path)))

    assert result.summary == "Corrected summary"
    assert len(client.requests) == 2
    assert "Too vague" in client.requests[1]["input_text"]
    assert client.sessions == [{"name": "run-7:example:extraction-session", "session_id": "run-7"}]


async def test_extraction_ignores_bad_indexes_in_fields_it_does_not_take(tmp_path, monkeypatch):
    # Only the summary was flagged, so only the summary is taken from the retry.
    # A bad claim index in that retry is never used, so the run must not fail.
    monkeypatch.setattr(analyzer_module, "OPENAI_CALL_LIMIT", 2)
    first = partial_facts()
    retry = partial_facts(claim_index=9).model_copy(update={"summary": "Corrected summary"})
    client = ScriptedClient(
        [first, retry],
        verdicts=[ExtractionFeedbacks(feedbacks=[ExtractionFeedback(what="summary", why="Too vague")])],
    )

    result = await Analyzer(client).extract_information(load_analysis_input(one_page_export(tmp_path)))

    assert result.summary == "Corrected summary"
    assert result.claims_cited[0].claim_page_url == "https://example.com/"


async def test_extraction_fixes_a_bad_citation_on_retry(tmp_path):
    client = ScriptedClient([partial_facts(claim_index=5), partial_facts(claim_index=0)])

    result = await Analyzer(client).extract_information(load_analysis_input(one_page_export(tmp_path)))

    assert result.claims_cited[0].claim_page_url == "https://example.com/"
    assert len(client.requests) == 2


async def test_extraction_gives_up_after_the_call_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer_module, "OPENAI_CALL_LIMIT", 2)
    client = ScriptedClient([partial_facts(claim_index=-1)])

    with pytest.raises(ValueError, match="Claim page index -1 is out of range"):
        await Analyzer(client).extract_information(load_analysis_input(one_page_export(tmp_path)))
    assert len(client.requests) == 2


async def test_comparison_uses_the_corrected_reply_and_the_run_session():
    client = ScriptedClient(
        [swot(page_index=9), swot(page_index=0)],
        verdicts=[SWOTFeedbacks(feedbacks=[SWOTFeedback(what="SWOT_entries", why="Cite the page")])],
    )
    analyzer = Analyzer(client, trace_prefix="run-7", session_id="run-7")

    result = await analyzer.compare(DATA)

    assert result.SWOT_entries[0].swot_component_page_urls == ["https://vanta.com/"]
    assert len(client.requests) == 2
    assert "Page number 9 does not exist" in client.requests[1]["input_text"]
    assert client.sessions == [{"name": "comparison-session", "session_id": "run-7"}]
    # The reviewer sees every competitor, not just one.
    assert "Vanta" in client.reviews[0] and "Drata" in client.reviews[0]


async def test_comparison_fails_cleanly_when_citations_stay_broken(monkeypatch):
    monkeypatch.setattr(analyzer_module, "OPENAI_CALL_LIMIT", 2)
    client = ScriptedClient([swot(page_index=9)])

    with pytest.raises(ValueError, match="SWOT output still has violations after retries"):
        await Analyzer(client).compare(DATA)
    assert len(client.requests) == 2
