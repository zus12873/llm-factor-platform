"""Factor platform command-line interface.

``parse-case`` runs a golden case fully offline: the canned draft drives the parser,
the clarification engine audits the result, and the command exits non-zero if the
actual blocking questions diverge from the fixture. This is the Week-1 acceptance
gate ("CLI parses ten cases and blocks ambiguous inputs").

``build-wind-catalog`` parses the committed Wind Markdown field index into a
JSONL catalog consumed by the field search layer. Fully offline.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from factor_platform.factor.clarification import ClarificationEngine
from factor_platform.factor.golden import get_golden_case, load_golden_cases
from factor_platform.factor.parser import FactorParser
from factor_platform.llm.base import FakeLLMProvider
from factor_platform.wind.catalog import CatalogBuilder, FieldCatalog

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


if __name__ == "__main__":  # pragma: no cover
    app()
