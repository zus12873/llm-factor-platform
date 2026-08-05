"""Canonical formula rendering.

The model emits only ``formula_ast``. The backend renders ``canonical_formula``
from that AST, and *that string* is what the user confirms. Rendering must
therefore be deterministic and total: the same AST always yields the same text,
and every legal AST is renderable.
"""

from __future__ import annotations

import pytest

from factor_platform.domain.formula import FormulaNode
from factor_platform.factor.renderer import render_canonical_formula


def var(name: str) -> FormulaNode:
    return FormulaNode(type="variable", name=name)


def lit(value: float) -> FormulaNode:
    return FormulaNode(type="literal", value=value)


def call(op: str, *args: FormulaNode, **params: float | int | str) -> FormulaNode:
    return FormulaNode(type="call", op=op, args=list(args), params=params)


def test_variable_renders_as_its_name() -> None:
    assert render_canonical_formula(var("close")) == "close"


def test_integral_literal_drops_the_decimal_tail() -> None:
    assert render_canonical_formula(lit(20.0)) == "20"
    assert render_canonical_formula(lit(0.5)) == "0.5"


def test_call_renders_args_then_sorted_params() -> None:
    node = call("rolling_return", var("close"), window=20)
    assert render_canonical_formula(node) == "rolling_return(close, window=20)"


def test_nested_call_matches_expected_text() -> None:
    node = call("rank", call("rolling_return", var("close"), window=20))
    assert render_canonical_formula(node) == "rank(rolling_return(close, window=20))"


def test_params_are_sorted_so_rendering_is_insertion_order_independent() -> None:
    declared_one_way = FormulaNode(
        type="call", op="fillna", args=[var("x")], params={"method": "ffill", "limit": 3}
    )
    declared_the_other_way = FormulaNode(
        type="call", op="fillna", args=[var("x")], params={"limit": 3, "method": "ffill"}
    )
    assert render_canonical_formula(declared_one_way) == render_canonical_formula(
        declared_the_other_way
    )
    assert render_canonical_formula(declared_one_way) == "fillna(x, limit=3, method=ffill)"


def test_rendering_is_deterministic_across_calls() -> None:
    node = call("divide", call("rolling_mean", var("close"), window=5), var("volume"))
    assert render_canonical_formula(node) == render_canonical_formula(node)


def test_every_registered_operator_is_renderable() -> None:
    from factor_platform.domain.formula import FORMULA_OPERATORS

    for op in FORMULA_OPERATORS:
        node = call(op, var("x"), var("y")) if op in _BINARY_OPS else call(op, var("x"))
        rendered = render_canonical_formula(node)
        assert rendered.startswith(f"{op}(")


_BINARY_OPS = {"add", "subtract", "multiply", "divide"}


def test_removed_operators_cannot_even_be_constructed() -> None:
    from pydantic import ValidationError

    for removed in ("zscore", "winsorize", "industry_neutralize"):
        with pytest.raises(ValidationError):
            FormulaNode(type="call", op=removed, args=[var("x")])
