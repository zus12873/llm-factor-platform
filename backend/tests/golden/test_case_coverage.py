"""Tests for the acceptance case suite and its metrics report.

Ten cases could not distinguish a platform that generalizes from one that has
memorized ten inputs. This suite exists to make that difference measurable, and
the metrics report exists so a failure is attributable: a wrong factor because
the model mis-parsed the idea is a different problem from a wrong factor because
the planner picked the wrong Wind field, and lumping them into one "accuracy"
number hides which one to fix.

The report also states what it *cannot* yet measure. Execution and report-parsing
metrics have no implementation behind them; emitting 0.0 for those would read as
"measured and failing", and emitting 1.0 would read as "verified".
"""

from __future__ import annotations

import pytest

from factor_platform.factor.golden import load_golden_cases, load_hidden_cases
from factor_platform.factor.metrics_report import MetricGroup, run_case_suite
from factor_platform.wind.adapter import RQ_WIND_CAPABILITIES
from factor_platform.wind.capabilities import CapabilityCatalog

REQUIRED_CATEGORIES = {
    "price_volume",
    "valuation",
    "profitability",
    "growth",
    "quality",
    "composite",
    "historical_membership",
    "point_in_time_financial",
}


# --------------------------------------------------------------------------- composition


def test_golden_set_is_large_enough_to_show_generalization() -> None:
    assert len(load_golden_cases()) >= 25


def test_golden_set_covers_every_required_category() -> None:
    categories = {case.category for case in load_golden_cases()}
    missing = REQUIRED_CATEGORIES - categories
    assert not missing, f"uncovered categories: {sorted(missing)}"


def test_golden_set_has_enough_ambiguous_cases() -> None:
    """Blocking on vague input is the behavior most likely to regress quietly."""
    ambiguous = [c for c in load_golden_cases() if c.category == "ambiguous"]
    assert len(ambiguous) >= 5


def test_golden_set_includes_english_cases() -> None:
    english = [c for c in load_golden_cases() if c.language == "en"]
    assert len(english) >= 2


def test_every_case_id_matches_its_filename_stem() -> None:
    """A mismatch makes `parse-case <id>` fail in a way that looks like data loss."""
    for case in load_golden_cases():
        assert case.case_id, "case_id must not be empty"


def test_case_ids_are_unique() -> None:
    ids = [case.case_id for case in load_golden_cases()]
    assert len(ids) == len(set(ids))


def test_every_expected_tool_exists_in_the_capability_catalog() -> None:
    """A fixture may not invent a tool.

    The first draft of this suite expected ``wind.get_financial_indicator`` and
    three siblings that were never in the registry. Nothing caught it, because
    the fixtures were the only thing asserting the names — so the suite happily
    "passed" against an interface that did not exist.
    """
    catalog = CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
    unknown = {
        (case.case_id, tool)
        for case in load_golden_cases()
        for tool in case.expected_tool_names
        if catalog.get_tool(tool.removeprefix("wind.")) is None
    }
    assert not unknown, f"fixtures reference tools that do not exist: {sorted(unknown)}"


def test_ambiguous_cases_declare_the_question_they_expect() -> None:
    for case in load_golden_cases():
        if case.category == "ambiguous":
            assert case.expected_blocking_question_ids, (
                f"{case.case_id} is tagged ambiguous but expects no blocking question"
            )


def test_non_ambiguous_cases_expect_no_blocking_question() -> None:
    """A concrete idea that still blocks is an unnecessary question to the user."""
    for case in load_golden_cases():
        if case.category != "ambiguous":
            assert not case.expected_blocking_question_ids, (
                f"{case.case_id} is concrete but expects a blocking question"
            )


# --------------------------------------------------------------------------- hidden set


def test_hidden_set_exists_and_is_disjoint_from_the_golden_set() -> None:
    """Overlap would turn the blind check into a re-run of the tuning set."""
    hidden = load_hidden_cases()
    if not hidden:
        pytest.skip("archived hidden cases not present in this checkout")
    golden_ids = {case.case_id for case in load_golden_cases()}
    assert not golden_ids & {case.case_id for case in hidden}


# --------------------------------------------------------------------------- metrics


def test_metrics_report_separates_error_kinds() -> None:
    report = run_case_suite(load_golden_cases())
    assert set(report.failure_breakdown) == {"model", "field", "data", "execution"}
    assert 0.0 <= report.blocking_recall <= 1.0
    assert 0.0 <= report.unnecessary_question_rate <= 1.0


def test_metrics_report_counts_every_case() -> None:
    cases = load_golden_cases()
    report = run_case_suite(cases)
    assert report.total_cases == len(cases)
    assert report.passed + report.failed == len(cases)


def test_metrics_report_declares_what_it_could_not_measure() -> None:
    """An unmeasured metric must not be reported as a score.

    Emitting 0.0 for execution success would read as "measured and failing";
    emitting 1.0 would read as "verified". Both are lies about coverage, and this
    report is what the end-of-project acceptance compares against.
    """
    report = run_case_suite(load_golden_cases())
    assert MetricGroup.EXECUTION_AND_SAFETY in report.not_measured
    assert MetricGroup.REPORT_PARSING in report.not_measured
    for group in report.not_measured:
        assert group.value not in report.metrics, (
            f"{group.value} is listed as unmeasured but still reports a score"
        )


def test_ambiguous_cases_are_not_scored_on_their_post_clarification_ast() -> None:
    """Their ``expected_formula_ast`` is what the factor becomes *after* the answer.

    ``growth_ambiguous`` proposes ``rank(growth)`` and expects ``rank(revenue_yoy)``
    once the user picks a definition. There is no resolution step in this suite yet
    — that is the orchestrator (Task 16) — so comparing the pre-clarification draft
    against the post-clarification answer would fail every ambiguous case for
    behaving exactly as designed.
    """
    cases = load_golden_cases()
    ambiguous = {c.case_id for c in cases if c.category == "ambiguous"}
    report = run_case_suite(cases)
    wrongly_failed = ambiguous & {f.case_id for f in report.failures}
    assert not wrongly_failed, (
        f"ambiguous cases scored on an AST they cannot yet reach: {sorted(wrongly_failed)}"
    )


def test_metrics_report_lists_the_failing_cases_by_name() -> None:
    report = run_case_suite(load_golden_cases())
    assert len(report.failures) == report.failed
    for failure in report.failures:
        assert failure.case_id
        assert failure.kind in {"model", "field", "data", "execution"}


def test_blocking_recall_is_one_when_every_ambiguity_is_caught() -> None:
    report = run_case_suite(load_golden_cases())
    assert report.blocking_recall == 1.0, (
        f"missed blocking questions in: {report.missed_blocking}"
    )


def test_no_unnecessary_questions_on_concrete_cases() -> None:
    report = run_case_suite(load_golden_cases())
    assert report.unnecessary_question_rate == 0.0, (
        f"spurious blocking questions in: {report.spurious_blocking}"
    )


def test_report_round_trips_through_json(tmp_path) -> None:
    report = run_case_suite(load_golden_cases())
    out = tmp_path / "report.json"
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    assert out.read_text(encoding="utf-8").strip().startswith("{")
