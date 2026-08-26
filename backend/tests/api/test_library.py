"""API contract tests for the immutable factor library.

Publish is only legal from a completed session with a real parquet and a stored
manifest hash. The API must not weaken the copy-not-reference rule or the
disputed-metric gate, and an unknown version is a mapped 404 rather than a 500.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from factor_platform.db.repository import SessionRepository
from factor_platform.library.service import FactorLibrary
from factor_platform.llm.base import FakeLLMProvider
from factor_platform.main import create_app
from factor_platform.orchestration.states import EventType
from factor_platform.settings import Settings

SPEC = {
    "factor_name": "quality",
    "asset_type": "stock",
    "universe": "000300.SH",
    "frequency": "daily",
    "direction": "higher_is_better",
    "canonical_formula": "rank(roe_ttm)",
    "formula_ast": {
        "type": "call",
        "op": "rank",
        "args": [{"type": "variable", "name": "roe_ttm"}],
    },
    "variables": [{"logical_name": "roe_ttm", "meaning": "ROE"}],
}

CODE_SHA = "a" * 64
PROGRAM = "print('factor')"


@pytest.fixture
async def client(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    settings = Settings(app_env="test", library_root=str(tmp_path / "library"))
    app = create_app(settings=settings, engine=engine, provider=FakeLLMProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _parquet(tmp_path: Path) -> Path:
    path = tmp_path / "run" / "result.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PAR1-result-bytes")
    return path.resolve()


async def seed_completed(
    engine: AsyncEngine,
    session_id: str,
    parquet: Path,
    *,
    metric_review_status: dict[str, str] | None = None,
    code_sha256: str | None = CODE_SHA,
    generated_code: str | None = PROGRAM,
) -> None:
    """Drive the legal event path to COMPLETED with a real result file."""
    repo = SessionRepository(engine)
    await repo.create_session(session_id)
    review = metric_review_status if metric_review_status is not None else {"ROE_TTM": "unreviewed"}
    artifact_uri = str(parquet)
    execution_result = {
        "status": "completed",
        "artifact_uri": artifact_uri,
        "resource_stats": {"metric_review_status": review},
    }
    code_payload: dict = {"plan": {"steps": []}}
    if generated_code is not None:
        code_payload["generated_code"] = generated_code
    if code_sha256 is not None:
        code_payload["code_sha256"] = code_sha256

    events: list[tuple[EventType, dict]] = [
        (EventType.PARSE_STARTED, {"request": {
            "asset_type": "stock",
            "universe": "000300.SH",
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "research_idea": "quality",
        }}),
        (EventType.FORMULA_PROPOSED, {"factor_spec": SPEC}),
        (EventType.FORMULA_CONFIRMED, {"factor_spec": SPEC}),
        (EventType.FIELD_CANDIDATES_FOUND, {"field_candidates": []}),
        (EventType.FIELDS_CONFIRMED, {"field_selections": [
            {"logical_name": "roe_ttm", "table": "asharettmhis", "field": "s_fa_roe_ttm"}
        ]}),
        (EventType.CODE_GENERATED, code_payload),
        (EventType.EXECUTION_STARTED, {}),
        (EventType.EXECUTION_SUCCEEDED, {
            "execution_result": execution_result,
            "artifact_uri": artifact_uri,
        }),
        (EventType.VALIDATION_PASSED, {
            "execution_result": execution_result,
            "artifact_uri": artifact_uri,
        }),
    ]
    for version, (event, payload) in enumerate(events):
        await repo.append_event(session_id, event, payload, expected_version=version)


@pytest.fixture
async def completed_session(engine: AsyncEngine, tmp_path: Path) -> str:
    await seed_completed(engine, "s-done", _parquet(tmp_path))
    return "s-done"


async def test_publish_refuses_an_incomplete_session(client: AsyncClient) -> None:
    await client.post("/api/sessions", json={"session_id": "s1"})
    response = await client.post("/api/library", json={"session_id": "s1"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_not_completed"


async def test_publish_copies_the_artifact_and_lists_latest(
    client: AsyncClient, completed_session: str, tmp_path: Path
) -> None:
    response = await client.post(
        "/api/library", json={"session_id": completed_session, "factor_id": "quality"}
    )
    assert response.status_code == 201
    entry = response.json()
    assert entry["version"] == 1
    assert entry["review_status"] in {"unreviewed", "reviewed"}
    assert entry["factor_id"] == "quality"
    assert "run" not in entry["artifact_path"]

    listed = await client.get("/api/library")
    assert listed.status_code == 200
    assert any(item["factor_id"] == "quality" for item in listed.json())

    got = await client.get("/api/library/quality/v/1")
    assert got.status_code == 200
    assert got.json()["manifest_sha256"] == CODE_SHA

    # Delete the run artifact; library copy must still verify.
    (tmp_path / "run" / "result.parquet").unlink()
    library = FactorLibrary(tmp_path / "library")
    assert library.verify_artifact("quality", 1) is True
    stored = tmp_path / "library" / entry["artifact_path"]
    assert stored.exists()
    assert stored.read_bytes() == b"PAR1-result-bytes"


async def test_publish_refuses_a_disputed_metric(
    client: AsyncClient, engine: AsyncEngine, tmp_path: Path
) -> None:
    await seed_completed(
        engine,
        "s-float",
        _parquet(tmp_path),
        metric_review_status={"FLOAT_MV": "disputed"},
    )
    response = await client.post(
        "/api/library", json={"session_id": "s-float", "factor_id": "float_mv"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "publish_refused"


async def test_unknown_library_version_is_a_mapped_404(client: AsyncClient) -> None:
    response = await client.get("/api/library/quality/v/99")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "library_entry_not_found"


async def test_publish_refuses_when_the_manifest_hash_is_missing(
    client: AsyncClient, engine: AsyncEngine, tmp_path: Path
) -> None:
    """A completed run without a stored hash must not invent one."""
    await seed_completed(
        engine,
        "s-no-hash",
        _parquet(tmp_path),
        code_sha256=None,
    )
    response = await client.post("/api/library", json={"session_id": "s-no-hash"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_not_completed"
