"""The P0 closed loop, end to end, against fakes.

This is the test that would catch a seam nobody owns: each component passes its
own suite and the chain still breaks, because the planner emits a plan the
manifest builder rejects, or the runtime writes a frame the validators cannot
read. Only running the whole thing finds those.

Wind and the model are faked. What is *not* faked is every boundary between our
own components — the plan, the manifest, the signature, the Parquet, the reports.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from factor_platform.domain.models import FieldSelection, FieldTimeRole, ResearchRequest
from factor_platform.execution.job_store import JobStore
from factor_platform.execution.manifest import InputArtifact, ManifestBuilder, sign
from factor_platform.execution.worker import Worker
from factor_platform.factor.export import CodeExporter
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.factor.parser import FactorParser
from factor_platform.llm.base import FakeLLMProvider
from factor_platform.validation.data import DataValidator
from factor_platform.validation.formula import FormulaValidator
from factor_platform.validation.result import ResultValidator
from factor_platform.wind.adapter import RQ_WIND_CAPABILITIES
from factor_platform.wind.capabilities import CapabilityCatalog
from factor_platform.wind.planner import WindPlanner

SIGNING_KEY = "unit-test-only-signing-key"  # pragma: allowlist secret
DATES = pd.date_range("2024-01-01", periods=60, freq="B")
CODES = ["600519.SH", "000001.SZ", "000002.SZ", "600000.SH", "601318.SH"]

DRAFT = {
    "factor_name": "momentum_20d",
    "hypothesis": "过去20日涨幅高的股票延续",
    "direction": "higher_is_better",
    "rebalance_frequency": "monthly",
    "formula_explanation": "rank(rolling_return(close,20))",
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


def request() -> ResearchRequest:
    return ResearchRequest.model_validate(
        {
            "asset_type": "stock",
            "universe": "000300.SH",
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "research_idea": "构建过去20个交易日的动量因子",
        }
    )


def fake_prices() -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(3)
    walk = np.cumsum(rng.normal(0, 0.02, size=(len(DATES), len(CODES))), axis=0)
    return pd.DataFrame(100 * np.exp(walk), index=DATES, columns=CODES)


@pytest.fixture
def registry() -> MetricRegistry:
    return MetricRegistry.load()


async def test_the_whole_p0_loop_closes(tmp_path: Path, registry: MetricRegistry) -> None:
    """Idea → spec → plan → manifest → worker → validated result."""
    # 1. Parse. The model only supplies the AST; the canonical formula is ours.
    provider = FakeLLMProvider()
    provider.enqueue_content(json.dumps(DRAFT, ensure_ascii=False))
    spec = await FactorParser(provider).parse(request())
    assert spec.canonical_formula == "rank(rolling_return(close, window=20))"

    # 2. Plan retrieval from the confirmed field binding.
    selections = [
        FieldSelection(
            logical_name="close",
            table="ashareeodprices",
            field="s_dq_adjclose",
            time_role=FieldTimeRole.OBSERVATION,
        )
    ]
    planner = WindPlanner(CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES), registry)
    plan = planner.plan(spec, selections, request())
    assert plan.warmup_start is not None
    assert plan.warmup_start < plan.metadata["start_date"]

    # 3. Fake Wind writes the inputs; hash them, then build the manifest around
    #    those hashes — the same order production uses.
    inputs = tmp_path / "inputs" / "staging"
    inputs.mkdir(parents=True)
    fake_prices().to_parquet(inputs / "close.parquet")
    digest = hashlib.sha256((inputs / "close.parquet").read_bytes()).hexdigest()

    manifest = ManifestBuilder().build(
        spec,
        plan,
        selections,
        [InputArtifact(uri=(inputs / "close.parquet").as_uri(), sha256=digest, rows=len(DATES))],
    )

    # 4. Data-layer validation runs on the inputs, before anything is computed.
    data_report = DataValidator().validate({"close": fake_prices()})
    assert not [f for f in data_report.findings if f.severity.value == "error"]

    # 5. Formula-layer validation on the spec.
    formula_report = FormulaValidator().validate(spec)
    assert not [f for f in formula_report.findings if f.severity.value == "error"]

    # 6. Queue and execute in the no-secret worker.
    store = JobStore(tmp_path / "jobs")
    signed = sign(manifest, key=SIGNING_KEY)
    job_id = store.enqueue(
        session_id="e2e",
        session_version=1,
        manifest_sha256=manifest.sha256,
        input_sha256=digest,
        signed_payload=signed.payload,
        signature=signed.signature,
    )
    job_inputs = tmp_path / "inputs" / job_id
    job_inputs.mkdir(parents=True)
    (job_inputs / "close.parquet").write_bytes((inputs / "close.parquet").read_bytes())

    worker = Worker(
        store,
        signing_key=SIGNING_KEY,
        artifact_root=tmp_path / "artifacts",
        input_root=tmp_path / "inputs",
    )
    outcome = worker.run_once()
    assert outcome.status == "completed", outcome.error
    assert outcome.runtime is not None
    assert outcome.runtime.manifest_sha256 == manifest.sha256

    # 7. Result-layer validation on what the worker produced.
    factor = pd.read_parquet(tmp_path / "artifacts" / job_id / "result.parquet")
    result_report = ResultValidator(registry).validate(factor)
    assert not [f for f in result_report.findings if f.severity.value == "error"]

    # The first 20 rows have no full window and must be NaN, not a value computed
    # from partial history.
    assert factor.iloc[:20].isna().all().all()
    assert factor.iloc[20:].notna().any().any()

    # 8. The export traces back to the manifest that ran.
    exported = CodeExporter().render(manifest)
    assert manifest.sha256 in exported.source


async def test_a_disputed_metric_stops_before_any_data_is_fetched(
    registry: MetricRegistry,
) -> None:
    """The refusal must land while stopping is still free."""
    from factor_platform.domain.errors import DisputedMetricError

    provider = FakeLLMProvider()
    provider.enqueue_content(json.dumps(DRAFT, ensure_ascii=False))
    spec = await FactorParser(provider).parse(request())
    spec.variables[0].logical_name = "float_mv"
    spec.formula_ast.args[0].args[0].name = "float_mv"

    selections = [
        FieldSelection(
            logical_name="float_mv",
            table="ashareeodderivativeindicator",
            field="float_a_shr",
        )
    ]
    planner = WindPlanner(CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES), registry)
    with pytest.raises(DisputedMetricError):
        planner.plan(spec, selections, request())


async def test_a_look_ahead_time_convention_never_reaches_a_manifest(
    registry: MetricRegistry,
) -> None:
    """Validation catches it before it can be signed and queued."""
    provider = FakeLLMProvider()
    provider.enqueue_content(json.dumps(DRAFT, ensure_ascii=False))
    spec = await FactorParser(provider).parse(request())
    spec.variables[0].point_in_time_required = True
    spec.variables[0].announcement_date_required = False

    report = FormulaValidator().validate(spec)
    assert report.has_error("future_financial_data")
