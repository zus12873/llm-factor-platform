"""Factor platform command-line interface.

``parse-case`` runs a golden case fully offline: the canned draft drives the parser,
the clarification engine audits the result, and the command exits non-zero if the
actual blocking questions diverge from the fixture. This is the Week-1 acceptance
gate ("CLI parses ten cases and blocks ambiguous inputs").
"""

from __future__ import annotations

import asyncio
import json

import typer

from factor_platform.factor.clarification import ClarificationEngine
from factor_platform.factor.golden import get_golden_case, load_golden_cases
from factor_platform.factor.parser import FactorParser
from factor_platform.llm.base import FakeLLMProvider

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
        "formula_text": spec.formula_text,
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


if __name__ == "__main__":  # pragma: no cover
    app()
