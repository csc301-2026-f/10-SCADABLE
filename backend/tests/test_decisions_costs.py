"""Offline tests for Decisions correlation, retries, and partial costs."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.costs import DecisionsCost, _fetch_decisions_from_api


class API:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

        class Observations:
            async def get_many(_, **kwargs):
                self.calls.append(kwargs)
                return self.pages[len(self.calls) - 1]

        self.observations = Observations()


def page(*observations, cursor=None):
    return SimpleNamespace(data=list(observations), meta=SimpleNamespace(cursor=cursor))


def attempt(*, obs_id, price, run_id="42", parent="b"*16, trace="a"*32, name="scraper.decisions.content"):
    return SimpleNamespace(
        id=obs_id, name=name, total_cost=price, trace_id=trace,
        parent_observation_id=parent,
        metadata={"research_run_id": run_id, "component": "scraper_decisions"},
    )


REF = {"trace_id": "a"*32, "observation_id": "b"*16, "research_run_id": "42"}


def lookup(api, expected=2, ref=REF):
    return asyncio.run(_fetch_decisions_from_api(
        api, run_id=42, reference=ref, expected_requests=expected,
        started_at=datetime.now(timezone.utc),
    ))


def test_costs_include_retries_and_cursor_continuation_with_empty_page():
    api = API([
        page(attempt(obs_id="1", price=0.004), cursor="keep-going"),
        page(cursor="continue-even-though-empty"),
        page(attempt(obs_id="2", price=0.003), attempt(obs_id="2", price=0.003)),
    ])
    result = lookup(api)
    assert result == DecisionsCost(found=2, usd=0.007, complete=True, expected=2)
    assert all(call["parent_observation_id"] == REF["observation_id"] for call in api.calls)
    assert all(call["from_start_time"] < call["to_start_time"] for call in api.calls)


def test_missing_price_is_not_complete_or_free():
    result = lookup(API([page(attempt(obs_id="1", price=0.002), attempt(obs_id="2", price=None))]))
    assert result.found == 2
    assert result.usd is None
    assert not result.complete


def test_tracking_reference_prevents_other_runs_or_parents_from_being_counted():
    api = API([page(
        attempt(obs_id="one", price=0.002),
        attempt(obs_id="other-run", price=9, run_id="43"),
        attempt(obs_id="other-parent", price=9, parent="c"*16),
    )])
    result = lookup(api, expected=1)
    assert result == DecisionsCost(found=1, usd=0.002, complete=True, expected=1)


def test_unobserved_calls_remain_incomplete():
    result = lookup(API([page()]), expected=1)
    assert result.found == 0
    assert result.usd == 0
    assert not result.complete


def test_expected_zero_is_valid_measured_zero():
    result = lookup(API([page()]), expected=0)
    assert result == DecisionsCost(found=0, usd=0, complete=True, expected=0)


def test_missing_reference_finds_attempts_by_run_metadata():
    api = API([
        page(attempt(obs_id="1", price=0.001, name="scraper.decisions.candidates")),
        page(attempt(obs_id="2", price=0.002, name="scraper.decisions.content")),
    ])
    result = lookup(api, expected=2, ref=None)
    assert result == DecisionsCost(found=2, usd=0.003, complete=True, expected=2)
    assert [call["name"] for call in api.calls] == [
        "scraper.decisions.candidates", "scraper.decisions.content"
    ]


def test_unknown_expected_count_does_not_prove_zero_cost():
    result = lookup(API([page()]), expected=None)
    assert result.found == 0
    assert not result.complete
