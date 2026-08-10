"""Factor platform command-line interface.

``parse-case`` runs a golden case fully offline: the canned draft drives the parser,
the clarification engine audits the result, and the command exits non-zero if the
actual blocking questions diverge from the fixture. This is the Week-1 acceptance
gate ("CLI parses ten cases and blocks ambiguous inputs").

``build-wind-catalog`` parses the Wind Markdown field index into a JSONL catalog
consumed by the field search layer.

``sync-wds-metadata`` parses the local Wind data dictionary into a metadata
catalog and merges it onto that field index. It reports coverage rather than
hiding it: the dictionary is a PDF extraction and cannot describe every field, so
a silent run would leave the operator believing the metadata tier is complete.

Both are fully offline; neither opens a database connection or a network socket.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from factor_platform.domain.models import (
    FieldCandidate,
    FieldSelection,
    QueryShape,
    ValidationSeverity,
)
from factor_platform.factor.clarification import ClarificationEngine
from factor_platform.factor.golden import (
    get_golden_case,
    load_golden_cases,
    load_hidden_cases,
)
from factor_platform.factor.metric_registry import MetricRegistry
from factor_platform.factor.metrics_report import run_case_suite
from factor_platform.factor.parser import FactorParser
from factor_platform.llm.base import FakeLLMProvider
from factor_platform.settings import get_settings
from factor_platform.validation.formula import FormulaValidator
from factor_platform.wind.adapter import RQ_WIND_CAPABILITIES
from factor_platform.wind.capabilities import CapabilityCatalog
from factor_platform.wind.catalog import CatalogBuilder, FieldCatalog
from factor_platform.wind.metadata_catalog import MetadataCatalog
from factor_platform.wind.metadata_repository import MetadataRepository
from factor_platform.wind.planner import WindPlanner
from factor_platform.wind.query_executor import WindNotConfiguredError, WindQueryExecutor
from factor_platform.wind.sample_verify import SampleVerdict, SampleVerifier, plan_for_shape
from factor_platform.wind.schema_verify import SchemaVerdict, SchemaVerifier
from factor_platform.wind.wds_sync import DictionaryBuilder

app = typer.Typer(add_completion=False, help="Factor platform command line interface.")


@app.command("parse-case")
def parse_case(case_id: str) -> None:
    """Parse one golden case offline and print its clarification result."""
    case = get_golden_case(case_id)
    provider = FakeLLMProvider()
    provider.enqueue_content(json.dumps(case.provider_draft, ensure_ascii=False))
    spec = asyncio.run(FactorParser(provider).parse(case.request))
    questions = ClarificationEngine().questions(spec)
    actual_blocking = sorted(q.question_id for q in questions if q.blocking)
    expected = sorted(case.expected_blocking_question_ids)
    payload = {
        "case_id": case.case_id,
        "factor_name": spec.factor_name,
        "canonical_formula": spec.canonical_formula,
        "blocking_questions": actual_blocking,
        "expected_blocking": expected,
        "match": actual_blocking == expected,
        "all_questions": [q.model_dump() for q in questions],
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if actual_blocking != expected:
        raise typer.Exit(code=1)


@app.command("list-cases")
def list_cases() -> None:
    """List available golden cases."""
    for case in load_golden_cases():
        typer.echo(f"{case.case_id}\t{case.description}")


@app.command("build-wind-catalog")
def build_wind_catalog(
    source: Annotated[
        Path,
        typer.Option(
            help="Path to windquery/references/wind_field_index.md",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            help="Where to write the generated JSONL catalog",
        ),
    ],
) -> None:
    """Parse the Wind field index Markdown into a normalized JSONL catalog."""
    records = CatalogBuilder(source).build()
    if not records:
        typer.echo(f"error: no records parsed from {source}", err=True)
        raise typer.Exit(code=1)
    FieldCatalog(records).save(output)
    typer.echo(
        f"wrote {len(records)} records from {len({r.table for r in records})} "
        f"tables to {output}"
    )


@app.command("run-case")
def run_case(
    case_id: Annotated[str, typer.Argument(help="Golden case id")],
    real_wind: Annotated[
        bool, typer.Option("--real-wind", help="Fetch from the live Wind replica")
    ] = False,
) -> None:
    """Drive one golden case through the full P0 chain and report each stage.

    Without ``--real-wind`` the retrieval step is skipped and the chain is
    exercised as far as the signed manifest — which covers every seam between our
    own components. The live fetch is the one part that needs credentials.
    """
    case = get_golden_case(case_id)

    provider = FakeLLMProvider()
    provider.enqueue_content(json.dumps(case.provider_draft, ensure_ascii=False))
    spec = asyncio.run(FactorParser(provider).parse(case.request))

    blocking = [q for q in ClarificationEngine().questions(spec) if q.blocking]
    typer.echo(f"parse    : {spec.canonical_formula}")
    if blocking:
        typer.echo(
            "clarify  : blocked on "
            + ", ".join(q.question_id for q in blocking)
            + "\n           (an under-specified idea stops here by design)"
        )
        raise typer.Exit(code=0)
    typer.echo("clarify  : no blocking question")

    formula_report = FormulaValidator().validate(spec)
    errors = [f for f in formula_report.findings if f.severity is ValidationSeverity.ERROR]
    for finding in formula_report.findings:
        typer.echo(f"validate : [{finding.severity.value}] {finding.code}: {finding.message}")
    if errors:
        raise typer.Exit(code=1)

    selections = [
        FieldSelection.model_validate(field)
        for field in case.expected_fields
        if field.get("table") and field.get("field")
    ]
    if not selections:
        typer.echo("plan     : skipped (case pins no confirmed fields)")
        raise typer.Exit(code=0)

    registry = MetricRegistry.load()
    planner = WindPlanner(CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES), registry)
    plan = planner.plan(spec, selections, case.request)
    typer.echo(
        f"plan     : {len(plan.steps)} step(s) — "
        + ", ".join(step.tool for step in plan.steps)
        + f"\n           warm-up from {plan.warmup_start}"
    )

    if not real_wind:
        typer.echo(
            "fetch    : skipped (--real-wind not given; this is the only stage "
            "that needs credentials)"
        )
        raise typer.Exit(code=0)

    typer.echo(
        "fetch    : live Wind retrieval is not wired into this command yet; "
        "see docs/acceptance/deferred-credential-steps.md",
        err=True,
    )
    raise typer.Exit(code=2)


@app.command("verify-field")
def verify_field(
    table: Annotated[str, typer.Argument(help="Wind table name")],
    field: Annotated[str, typer.Argument(help="Wind column name")],
    shape: Annotated[
        str, typer.Option(help="Query shape driving the sample plan")
    ] = "point_range",
) -> None:
    """Verify one Wind field: schema first, then a bounded data sample.

    The two results are reported separately and only structural failures are
    fatal. "Column exists, your window has no rows" exits zero — it is a fact for
    the user, not a verdict against the field.

    This is the one command in the offline toolchain that needs live credentials.
    """
    try:
        query_shape = QueryShape(shape)
    except ValueError:
        typer.echo(
            f"error: unknown shape {shape!r}; expected one of "
            f"{', '.join(s.value for s in QueryShape)}",
            err=True,
        )
        raise typer.Exit(code=2) from None

    # A missing .env surfaces here as a validation error about the session cookie
    # secret, which tells the user nothing about the Wind field they asked to
    # verify. Translate it into the actual problem.
    try:
        settings = get_settings()
    except ValidationError as exc:
        typer.echo(
            "error: settings could not be loaded — copy .env.example to .env and "
            f"fill in the Wind credentials.\n{exc.error_count()} field(s) invalid.",
            err=True,
        )
        raise typer.Exit(code=3) from None

    try:
        executor = WindQueryExecutor(settings)
    except WindNotConfiguredError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=3) from None

    async def _run() -> tuple[SchemaVerdict, SampleVerdict | None]:
        candidate = FieldCandidate(table=table, field=field)
        schema = await SchemaVerifier(
            executor, database=settings.wind_database or ""
        ).verify(candidate)
        if schema.is_blocking:
            return schema, None
        sample = await SampleVerifier(executor).verify(
            candidate, plan_for_shape(query_shape)
        )
        return schema, sample

    schema_verdict, sample_verdict = asyncio.run(_run())
    typer.echo(f"schema: {schema_verdict.status.value} — {schema_verdict.detail}")
    if sample_verdict is None:
        raise typer.Exit(code=1)
    typer.echo(
        f"sample: {sample_verdict.status.value} — {sample_verdict.detail}\n"
        f"  plan  : {sample_verdict.plan.security_count} securities × "
        f"{sample_verdict.plan.period_count} "
        f"{sample_verdict.plan.period_kind or 'periods'} "
        f"({sample_verdict.plan.rationale})"
        if sample_verdict.plan
        else f"sample: {sample_verdict.status.value}"
    )


@app.command("run-case-suite")
def run_suite(
    case_set: Annotated[
        str, typer.Option("--set", help="Which suite to run: golden | hidden")
    ] = "golden",
    report: Annotated[
        Path | None, typer.Option(help="Write the structured report here")
    ] = None,
) -> None:
    """Run an acceptance suite offline and print its metrics report.

    The hidden suite is the blind check: run it once, at final acceptance, and
    compare against the golden baseline. A large gap between the two is the
    signal that the implementation was fitted to the cases it could see.
    """
    if case_set not in {"golden", "hidden"}:
        typer.echo(f"error: unknown set {case_set!r} (expected golden or hidden)", err=True)
        raise typer.Exit(code=2)

    cases = load_golden_cases() if case_set == "golden" else load_hidden_cases()
    if not cases:
        typer.echo(
            f"error: {case_set} set is empty"
            + (" (it is gitignored; supply it locally)" if case_set == "hidden" else ""),
            err=True,
        )
        raise typer.Exit(code=1)

    result = run_case_suite(cases, suite=case_set)
    typer.echo(
        f"{case_set}: {result.passed}/{result.total_cases} passed "
        f"({result.elapsed_seconds:.2f}s)\n"
        f"  blocking recall     : {result.blocking_recall:.1%}\n"
        f"  blocking precision  : {result.blocking_precision:.1%}\n"
        f"  unnecessary questions: {result.unnecessary_question_rate:.1%}\n"
        f"  failures by kind    : {result.failure_breakdown}"
    )
    for group in result.not_measured:
        typer.echo(f"  not measured — {group.value}: {result.not_measured_reason[group.value]}")
    for failure in result.failures:
        typer.echo(f"  FAIL [{failure.kind}] {failure.case_id}: {failure.detail}")

    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"wrote report to {report}")

    if result.failed:
        raise typer.Exit(code=1)


@app.command("sync-wds-metadata")
def sync_wds_metadata(
    source: Annotated[
        Path,
        typer.Option(help="Directory of Wind 数据字典 Markdown files (or one file)"),
    ],
    index: Annotated[
        Path,
        typer.Option(help="Generated field-index JSONL from build-wind-catalog"),
    ],
    output: Annotated[
        Path,
        typer.Option(help="Where to write the merged metadata JSONL"),
    ],
    units: Annotated[
        Path | None,
        typer.Option(help="Optional reviewable unit overlay YAML"),
    ] = None,
) -> None:
    """Localize the Wind data dictionary and merge it onto the field index."""
    described = DictionaryBuilder(source, units_path=units).build()
    if not described:
        typer.echo(f"error: no metadata parsed from {source}", err=True)
        raise typer.Exit(code=1)

    merged = MetadataRepository.merge(FieldCatalog.load(index).records, described)
    MetadataCatalog(list(merged.values())).save(output)

    coverage = MetadataRepository.coverage(merged)
    undescribed = sum(1 for m in merged.values() if m.metadata_source is None)
    typer.echo(
        f"wrote {len(merged)} fields to {output}\n"
        f"  described by dictionary : {len(merged) - undescribed} ({coverage:.1%})\n"
        f"  no metadata recovered   : {undescribed} (kept and flagged, not dropped)"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
