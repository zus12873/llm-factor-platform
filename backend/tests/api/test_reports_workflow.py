"""API tests: persisted report extraction seeds a normal workbench session."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from factor_platform.api import reports
from factor_platform.llm.base import FakeLLMProvider
from factor_platform.main import create_app
from factor_platform.reports.extractor import ExtractedFactor
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

ENVELOPE = {
    "asset_type": "stock",
    "universe": "000300.SH",
    "start_date": "2024-01-01",
    "end_date": "2024-06-30",
    "research_idea": "研报抽取",
}

AST = {
    "type": "call",
    "op": "rank",
    "args": [{"type": "variable", "name": "roe_ttm"}],
}


@pytest.fixture
def upload_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
async def client(engine: AsyncEngine, upload_root: Path) -> AsyncIterator[AsyncClient]:
    provider = FakeLLMProvider()
    for _ in range(6):
        provider.enqueue_content(json.dumps(DRAFT, ensure_ascii=False))
    app = create_app(settings=Settings(app_env="test"), engine=engine, provider=provider)
    app.dependency_overrides[reports.get_upload_root] = lambda: upload_root
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _extracted(tmp_path: Path, artifact_id: str, status: str, text: str = "rank(roe_ttm)") -> None:
    extraction = ExtractedFactor.model_validate(
        {
            "factor_name": "quality",
            "hypothesis": "ROE 高的股票更好",
            "direction": "higher_is_better",
            "variables": [{"logical_name": "roe_ttm", "meaning": "净资产收益率TTM"}],
            "evidence": [
                {
                    "evidence_id": "p3b1",
                    "page_number": 3,
                    "text": "因子定义：对 ROE_TTM 做横截面排名。",
                    "score": 5.0,
                    "bbox": [0, 0, 1, 1],
                }
            ],
            "formula_extraction": {
                "status": status,
                "confidence": 0.92 if status == "extracted" else 0.4,
                "source_pages": [3],
                "extracted_text": text,
                "formula_ast": AST if status == "extracted" else None,
                "warning": "" if status == "extracted" else "需人工确认",
            },
        }
    )
    (tmp_path / f"{artifact_id}.extraction.json").write_text(
        extraction.model_dump_json(), encoding="utf-8"
    )
    (tmp_path / f"{artifact_id}.pdf").write_bytes(b"%PDF-")


async def test_unconfirmed_extraction_cannot_enter_workflow(client, upload_root):
    _extracted(upload_root, "abc", "needs_manual_confirmation")
    response = await client.post(
        "/api/reports/abc/sessions",
        json={"session_id": "s-report", "request": ENVELOPE},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "report_formula_unconfirmed"


async def test_extracted_report_opens_a_normal_session_waiting_formula(client, upload_root):
    _extracted(upload_root, "abc", "extracted")
    response = await client.post(
        "/api/reports/abc/sessions",
        json={"session_id": "s-report", "request": ENVELOPE},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] == "s-report"
    assert body["request"]["report_artifact_id"] == "abc"
    assert body["state"] in {"needs_clarification", "waiting_formula_confirmation"}
    assert body["factor_spec"]["canonical_formula"]
    assert body["factor_spec"]["source_evidence"][0]["page_number"] == 3
    # Must not skip formula confirmation.
    assert body["state"] != "searching_fields"


async def test_manual_formula_is_required_and_then_parsed(client, upload_root):
    _extracted(upload_root, "abc", "needs_manual_confirmation")
    response = await client.post(
        "/api/reports/abc/sessions",
        json={
            "session_id": "s-manual",
            "request": ENVELOPE,
            "manual_formula": "rank(roe_ttm)",
        },
    )
    assert response.status_code == 201
    assert response.json()["request"]["research_idea"] == "rank(roe_ttm)"


async def test_missing_artifact_is_404(client):
    response = await client.post(
        "/api/reports/missing/sessions",
        json={"session_id": "s-x", "request": ENVELOPE},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "report_artifact_not_found"
