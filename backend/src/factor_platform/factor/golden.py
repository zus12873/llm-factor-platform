"""Golden factor cases: request + expected clarification/formula/tool contracts.

Each case pins a research idea to the deterministic behavior the platform must
reproduce: which blocking clarifications it raises, the post-confirmation formula,
and the Wind tools/fields the planner should select. Cases are plain JSON so they
double as acceptance fixtures and (via ``provider_draft``) as the canned LLM response
that lets the CLI run offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from factor_platform.domain.formula import FormulaNode
from factor_platform.domain.models import ResearchRequest


class GoldenCase(BaseModel):
    case_id: str
    description: str = ""
    request: ResearchRequest
    # The canned _FactorSpecDraft JSON the FakeLLMProvider returns for this case.
    provider_draft: dict[str, Any]
    expected_blocking_question_ids: list[str] = []
    expected_formula_ast: FormulaNode
    expected_tool_names: list[str]
    expected_fields: list[dict[str, Any]] = []
    acceptance: list[str] = []

    @property
    def data_dir(self) -> Path:
        backend_root = Path(__file__).resolve().parents[3]
        return backend_root / "data" / "golden_cases"


def golden_cases_dir(override: str | Path | None = None) -> Path:
    backend_root = Path(__file__).resolve().parents[3]
    return Path(override) if override else backend_root / "data" / "golden_cases"


def load_golden_cases(directory: str | Path | None = None) -> list[GoldenCase]:
    root = golden_cases_dir(directory)
    cases: list[GoldenCase] = []
    for path in sorted(root.glob("*.json")):
        cases.append(GoldenCase.model_validate_json(path.read_text(encoding="utf-8")))
    return cases


def get_golden_case(case_id: str, directory: str | Path | None = None) -> GoldenCase:
    for case in load_golden_cases(directory):
        if case.case_id == case_id:
            return case
    raise KeyError(f"unknown golden case: {case_id}")


__all__ = ["GoldenCase", "get_golden_case", "golden_cases_dir", "load_golden_cases"]
