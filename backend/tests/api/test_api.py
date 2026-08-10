"""API contract tests.

Two things are contracts rather than implementation details, and both are here:

* **Error codes.** A frontend that branches on a message breaks when someone
  improves the wording. ``stale_session_version`` is part of the interface.
* **SSE resumption.** A browser that slept must be able to say where it stopped
  and receive exactly what it missed. Without that the stream is a broadcast, and
  a client that misses it is permanently behind with no way to know.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from factor_platform.llm.base import FakeLLMProvider
from factor_platform.main import create_app
from factor_platform.settings import Settings

DRAFT = {
    "factor_name": "momentum_20d",
    "direction": "higher_is_better",
    "rebalance_frequency": "monthly",
    "formula_ast": {
        "type": "call",
        "op": "rank",
        "args": [
            {
                "type": "call",
                "op": "rolling_return",
                "args": [{"type": "variable", "name": "close"}],
                "params": {"window": 20},
            }
        ],
    },
    "variables": [{"logical_name": "close", "meaning": "后复权收盘价"}],
}

REQUEST = {
    "asset_type": "stock",
    "universe": "000300.SH",
    "start_date": "2024-01-01",
    "end_date": "2024-06-30",
    "research_idea": "构建过去20个交易日的动量因子",
}


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    provider = FakeLLMProvider()
    for _ in range(6):
        provider.enqueue_content(json.dumps(DRAFT, ensure_ascii=False))
    app = create_app(
        settings=Settings(app_env="test"), engine=engine, provider=provider
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def start_session(client: AsyncClient, session_id: str = "s1") -> dict:
    created = await client.post("/api/sessions", json={"session_id": session_id})
    assert created.status_code == 201
    response = await client.post(
        f"/api/sessions/{session_id}/messages",
        json={"expected_version": created.json()["version"], "request": REQUEST},
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- health


async def test_health_reports_each_component(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    names = {c["name"] for c in response.json()["components"]}
    assert {"database", "wind", "llm", "job_queue"} <= names


async def test_health_never_exposes_connection_details(client: AsyncClient) -> None:
    """Whoever reads this at 2am will paste it into a chat window."""
    body = (await client.get("/api/health")).text.lower()
    for forbidden in ("password", "api_key", "secret", "mysql+pymysql://", "token"):
        assert forbidden not in body


# --------------------------------------------------------------------------- sessions


async def test_a_session_advances_through_parse(client: AsyncClient) -> None:
    snapshot = await start_session(client)
    assert snapshot["state"] == "waiting_formula_confirmation"
    assert snapshot["factor_spec"]["canonical_formula"]


async def test_a_stale_version_returns_409_with_a_stable_code(
    client: AsyncClient,
) -> None:
    """Two tabs on one session is the normal case, not an edge case."""
    snapshot = await start_session(client)
    response = await client.post(
        "/api/sessions/s1/confirm-formula",
        json={
            "expected_version": snapshot["version"] - 1,
            "factor_spec": snapshot["factor_spec"],
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_session_version"


async def test_an_illegal_transition_returns_409(client: AsyncClient) -> None:
    created = await client.post("/api/sessions", json={"session_id": "s-fresh"})
    response = await client.post(
        "/api/sessions/s-fresh/confirm-formula",
        json={"expected_version": created.json()["version"], "factor_spec": DRAFT | {
            "asset_type": "stock",
            "universe": "000300.SH",
            "frequency": "daily",
        }},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "illegal_transition"


async def test_a_disputed_metric_returns_422_with_its_own_code(
    client: AsyncClient,
) -> None:
    snapshot = await start_session(client, "s-disputed")
    confirmed = await client.post(
        "/api/sessions/s-disputed/confirm-formula",
        json={
            "expected_version": snapshot["version"],
            "factor_spec": snapshot["factor_spec"],
        },
    )
    searched = await client.post(
        "/api/sessions/s-disputed/field-candidates",
        json={"expected_version": confirmed.json()["version"], "candidates": []},
    )
    response = await client.post(
        "/api/sessions/s-disputed/confirm-fields",
        json={
            "expected_version": searched.json()["version"],
            "field_selections": [
                {
                    "logical_name": "float_mv",
                    "table": "ashareeodderivativeindicator",
                    "field": "float_a_shr",
                }
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "disputed_metric"


async def test_revising_the_formula_returns_a_cascaded_snapshot(
    client: AsyncClient,
) -> None:
    snapshot = await start_session(client, "s-revise")
    confirmed = await client.post(
        "/api/sessions/s-revise/confirm-formula",
        json={
            "expected_version": snapshot["version"],
            "factor_spec": snapshot["factor_spec"],
        },
    )
    searched = await client.post(
        "/api/sessions/s-revise/field-candidates",
        json={"expected_version": confirmed.json()["version"], "candidates": []},
    )
    fields = await client.post(
        "/api/sessions/s-revise/confirm-fields",
        json={
            "expected_version": searched.json()["version"],
            "field_selections": [
                {
                    "logical_name": "close",
                    "table": "ashareeodprices",
                    "field": "s_dq_adjclose",
                }
            ],
        },
    )
    revised = await client.post(
        "/api/sessions/s-revise/revise-formula",
        json={
            "expected_version": fields.json()["version"],
            "factor_spec": snapshot["factor_spec"],
        },
    )
    assert revised.status_code == 200
    assert revised.json()["field_selections"] == []


# --------------------------------------------------------------------------- events


async def test_events_replay_the_whole_log(client: AsyncClient) -> None:
    await start_session(client, "s-events")
    response = await client.get("/api/sessions/s-events/events?replay_only=true")
    assert response.status_code == 200
    assert "id: 1" in response.text
    assert "event: parse_started" in response.text


async def test_events_resume_after_last_event_id(client: AsyncClient) -> None:
    """A browser that slept says where it stopped and gets only what it missed."""
    await start_session(client, "s-resume")
    response = await client.get(
        "/api/sessions/s-resume/events?replay_only=true",
        headers={"Last-Event-ID": "1"},
    )
    assert "id: 1" not in response.text
    assert "id: 2" in response.text


async def test_an_unparseable_last_event_id_replays_from_the_start(
    client: AsyncClient,
) -> None:
    """Better to resend than to skip: a skipped event is invisible to the client."""
    await start_session(client, "s-bad-id")
    response = await client.get(
        "/api/sessions/s-bad-id/events?replay_only=true",
        headers={"Last-Event-ID": "not-a-number"},
    )
    assert "id: 1" in response.text


async def test_the_event_id_is_the_session_sequence(client: AsyncClient) -> None:
    """One ordering, not two that must be kept consistent."""
    snapshot = await start_session(client, "s-seq")
    response = await client.get("/api/sessions/s-seq/events?replay_only=true")
    ids = [
        int(line.removeprefix("id: "))
        for line in response.text.splitlines()
        if line.startswith("id: ")
    ]
    assert ids == list(range(1, snapshot["version"] + 1))
