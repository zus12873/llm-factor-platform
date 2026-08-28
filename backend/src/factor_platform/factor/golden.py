"""Golden factor cases: request + expected clarification/formula/tool contracts.

Each case pins a research idea to the deterministic behavior the platform must
reproduce: which blocking clarifications it raises, the post-confirmation formula,
and the Wind tools/fields the planner should select. Cases are plain JSON so they
double as acceptance fixtures and (via ``provider_draft``) as the canned LLM response
that lets the CLI run offline.

Two sets exist. The **golden** set is visible during development and is what the
implementation is tuned against. The files under ``hidden_cases`` are tracked
historical acceptance fixtures. They are no longer blind and must not be reported
as an independent hidden-set result. A new blind acceptance set has to be supplied
outside the repository by someone who did not tune the implementation.
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
    # Which factor family this exercises. ``ambiguous`` means the idea is
    # deliberately under-specified and the platform must stop and ask.
    category: str = "price_volume"
    language: str = "zh"
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


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[3]


def golden_cases_dir(override: str | Path | None = None) -> Path:
    return Path(override) if override else _backend_root() / "data" / "golden_cases"


def hidden_cases_dir(override: str | Path | None = None) -> Path:
    return Path(override) if override else _backend_root() / "data" / "hidden_cases"


def _load_from(root: Path) -> list[GoldenCase]:
    if not root.exists():
        return []
    return [
        GoldenCase.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("*.json"))
    ]


def load_golden_cases(directory: str | Path | None = None) -> list[GoldenCase]:
    return _load_from(golden_cases_dir(directory))


def load_hidden_cases(directory: str | Path | None = None) -> list[GoldenCase]:
    """Load the archived hidden cases.

    These files are tracked historical fixtures, not a blind set. An empty
    directory still returns ``[]`` so a checkout without the files does not
    crash the suite.
    """
    return _load_from(hidden_cases_dir(directory))


def get_golden_case(case_id: str, directory: str | Path | None = None) -> GoldenCase:
    for case in load_golden_cases(directory):
        if case.case_id == case_id:
            return case
    raise KeyError(f"unknown golden case: {case_id}")


__all__ = [
    "GoldenCase",
    "get_golden_case",
    "golden_cases_dir",
    "hidden_cases_dir",
    "load_golden_cases",
    "load_hidden_cases",
]
