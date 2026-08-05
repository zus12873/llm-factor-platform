"""AST legality checks beyond structural well-formedness.

``FormulaNode`` guarantees each node is *shaped* correctly. These checks answer
the next question: is the tree as a whole sane, bound to declared variables, and
within the complexity and numeric limits the compiler can honour.
"""

from __future__ import annotations

from factor_platform.domain.formula import FormulaNode
from factor_platform.domain.models import DataRequirement
from factor_platform.factor.ast_checks import (
    MAX_DEPTH,
    MAX_NODES,
    MAX_WINDOW,
    check_ast,
)


def var(name: str) -> FormulaNode:
    return FormulaNode(type="variable", name=name)


def call(op: str, *args: FormulaNode, **params: float | int | str) -> FormulaNode:
    return FormulaNode(type="call", op=op, args=list(args), params=params)


def requirements(*names: str) -> list[DataRequirement]:
    return [DataRequirement(logical_name=name, meaning=name) for name in names]


# --------------------------------------------------------------- variable binding


def test_unbound_variable_is_blocking() -> None:
    report = check_ast(call("rank", var("undeclared")), requirements("close"))
    assert report.has_error("unbound_variable")


def test_bound_variable_passes() -> None:
    report = check_ast(call("rank", var("close")), requirements("close"))
    assert not report.has_error("unbound_variable")


def test_duplicate_variable_declaration_is_blocking() -> None:
    report = check_ast(call("rank", var("close")), requirements("close", "close"))
    assert report.has_error("duplicate_variable")


def test_declared_but_unused_variable_only_warns() -> None:
    report = check_ast(call("rank", var("close")), requirements("close", "volume"))
    assert report.has_warning("unused_variable")
    assert not report.has_error("unused_variable")


def test_variable_name_must_follow_the_naming_convention() -> None:
    report = check_ast(call("rank", var("Close Price")), requirements("Close Price"))
    assert report.has_error("invalid_variable_name")


# --------------------------------------------------------------- complexity


def test_excessive_rolling_window_is_blocking() -> None:
    report = check_ast(
        call("rolling_return", var("close"), window=MAX_WINDOW + 1), requirements("close")
    )
    assert report.has_error("window_out_of_range")


def test_zero_window_is_blocking() -> None:
    report = check_ast(call("rolling_std", var("close"), window=0), requirements("close"))
    assert report.has_error("window_out_of_range")


def test_rolling_operator_without_window_is_blocking() -> None:
    report = check_ast(call("rolling_mean", var("close")), requirements("close"))
    assert report.has_error("missing_window")


def test_deeply_nested_tree_is_blocking() -> None:
    node: FormulaNode = var("close")
    for _ in range(MAX_DEPTH + 2):
        node = call("negative", node)
    report = check_ast(node, requirements("close"))
    assert report.has_error("depth_exceeded")


def test_oversized_tree_is_blocking() -> None:
    node: FormulaNode = var("close")
    for _ in range(MAX_NODES + 2):
        node = call("add", node, var("close"))
    report = check_ast(node, requirements("close"))
    assert report.has_error("node_count_exceeded")


# --------------------------------------------------------------- numeric legality


def test_non_finite_literal_is_blocking() -> None:
    report = check_ast(
        call("multiply", var("close"), FormulaNode(type="literal", value=float("inf"))),
        requirements("close"),
    )
    assert report.has_error("non_finite_literal")


def test_nan_literal_is_blocking() -> None:
    report = check_ast(
        call("multiply", var("close"), FormulaNode(type="literal", value=float("nan"))),
        requirements("close"),
    )
    assert report.has_error("non_finite_literal")


# --------------------------------------------------------------- clean tree


def test_a_well_formed_momentum_tree_produces_no_errors() -> None:
    node = call("rank", call("rolling_return", var("close"), window=20))
    report = check_ast(node, requirements("close"))
    assert [f.code for f in report.findings if f.severity == "error"] == []
