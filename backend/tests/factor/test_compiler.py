"""Tests for the formula compiler — eleven operators, no eval.

The compiler turns a confirmed ``FormulaNode`` into a DataFrame indexed by date
with one column per security. Every operator is an explicit Python function
reached through a dict; there is no ``eval``, no dynamic import, and no path by
which model output becomes executable code.

Most of these tests pin axis semantics, because getting an axis wrong is the kind
of bug that produces a plausible number. ``rank`` across the wrong axis ranks each
stock against its own history instead of against its peers, and the output still
looks like a factor.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factor_platform.domain.formula import FormulaNode
from factor_platform.factor.compiler import CompilationError, FormulaCompiler


def var(name: str) -> FormulaNode:
    return FormulaNode(type="variable", name=name)


def call(op: str, *args: FormulaNode, **params: object) -> FormulaNode:
    return FormulaNode(type="call", op=op, args=list(args), params=dict(params))


def frame(rows: list[list[float]], *, dates: int | None = None) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=dates or len(rows), freq="D")
    return pd.DataFrame(rows, index=index, columns=["600519.SH", "000001.SZ"])


@pytest.fixture
def compiler() -> FormulaCompiler:
    return FormulaCompiler()


PRICES = frame([[10.0, 20.0], [11.0, 19.0], [12.0, 22.0], [12.0, 21.0]])


# --------------------------------------------------------------------------- axes


def test_rank_is_cross_sectional(compiler: FormulaCompiler) -> None:
    """Ranking down the time axis would score each stock against its own past."""
    values = frame([[1.0, 3.0], [4.0, 2.0]])
    result = compiler.evaluate(call("rank", var("x")), {"x": values})
    assert result.iloc[0].tolist() == [0.5, 1.0]
    assert result.iloc[1].tolist() == [1.0, 0.5]


def test_rolling_operators_run_along_time_per_security(
    compiler: FormulaCompiler,
) -> None:
    result = compiler.evaluate(
        call("rolling_mean", var("close"), window=2), {"close": PRICES}
    )
    assert result.iloc[1, 0] == pytest.approx((10.0 + 11.0) / 2)
    assert result.iloc[1, 1] == pytest.approx((20.0 + 19.0) / 2)


def test_rolling_return_window_is_a_lag_not_an_observation_count(
    compiler: FormulaCompiler,
) -> None:
    """``rolling_return(close, 20)`` is ``close[t]/close[t-20] - 1``.

    This differs on purpose from the rolling aggregates, whose window counts
    observations. Reading it as an observation count would make a "20-day
    momentum" span 19 periods — an off-by-one that produces a slightly wrong
    number forever and looks entirely reasonable.
    """
    result = compiler.evaluate(
        call("rolling_return", var("close"), window=2), {"close": PRICES}
    )
    assert result.iloc[2, 0] == pytest.approx(12.0 / 10.0 - 1)
    assert np.isnan(result.iloc[0, 0]), "the first row cannot know a prior price"
    assert np.isnan(result.iloc[1, 0]), "row 1 has no t-2 observation"


def test_a_partial_window_yields_nan_not_a_number(compiler: FormulaCompiler) -> None:
    """pandas defaults to ``min_periods=1``, which is the whole problem.

    With the default, a 20-day momentum on day 3 is computed from three days and
    returned as if it were a 20-day figure. Nothing downstream can tell the
    difference, and the early part of every backtest is quietly a different factor.
    """
    result = compiler.evaluate(
        call("rolling_mean", var("close"), window=3), {"close": PRICES}
    )
    assert np.isnan(result.iloc[0, 0])
    assert np.isnan(result.iloc[1, 0])
    assert not np.isnan(result.iloc[2, 0])


# --------------------------------------------------------------------------- arithmetic


def test_divide_replaces_infinities_with_nan(compiler: FormulaCompiler) -> None:
    """An infinite factor value survives ranking and poisons the whole section."""
    zeros = frame([[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]])
    result = compiler.evaluate(
        call("divide", var("x"), var("zero")), {"x": PRICES, "zero": zeros}
    )
    assert result.isna().all().all()


def test_log_of_a_non_positive_value_is_nan(compiler: FormulaCompiler) -> None:
    values = frame([[1.0, -1.0], [0.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    result = compiler.evaluate(call("log", var("x")), {"x": values})
    assert np.isnan(result.iloc[0, 1])
    assert np.isnan(result.iloc[1, 0])
    assert result.iloc[2, 0] == pytest.approx(np.log(3.0))


def test_add_subtract_multiply_are_elementwise(compiler: FormulaCompiler) -> None:
    a = frame([[1.0, 2.0], [3.0, 4.0]])
    b = frame([[10.0, 20.0], [30.0, 40.0]])
    variables = {"a": a, "b": b}
    assert compiler.evaluate(call("add", var("a"), var("b")), variables).iloc[0, 0] == 11.0
    assert (
        compiler.evaluate(call("subtract", var("b"), var("a")), variables).iloc[0, 0] == 9.0
    )
    assert (
        compiler.evaluate(call("multiply", var("a"), var("b")), variables).iloc[0, 0] == 10.0
    )


def test_negative_flips_the_sign(compiler: FormulaCompiler) -> None:
    result = compiler.evaluate(call("negative", var("x")), {"x": PRICES})
    assert result.iloc[0, 0] == -10.0


def test_fillna_replaces_missing_values(compiler: FormulaCompiler) -> None:
    values = frame([[1.0, np.nan], [np.nan, 4.0]])
    result = compiler.evaluate(call("fillna", var("x"), value=0.0), {"x": values})
    assert result.iloc[0, 1] == 0.0
    assert result.iloc[1, 0] == 0.0


# --------------------------------------------------------------------------- alignment


def test_operands_are_aligned_on_both_axes(compiler: FormulaCompiler) -> None:
    """Misaligned frames must not be combined positionally."""
    a = pd.DataFrame(
        [[1.0, 2.0]],
        index=pd.to_datetime(["2024-01-02"]),
        columns=["600519.SH", "000001.SZ"],
    )
    b = pd.DataFrame(
        [[10.0, 20.0]],
        index=pd.to_datetime(["2024-01-02"]),
        columns=["000001.SZ", "600519.SH"],
    )
    result = compiler.evaluate(call("add", var("a"), var("b")), {"a": a, "b": b})
    assert result.loc["2024-01-02", "600519.SH"] == pytest.approx(21.0)


# --------------------------------------------------------------------------- refusals


def test_an_unknown_operator_is_refused(compiler: FormulaCompiler) -> None:
    node = FormulaNode.model_construct(type="call", op="execute_sql", args=[var("x")])
    with pytest.raises(CompilationError, match="execute_sql"):
        compiler.evaluate(node, {"x": PRICES})


def test_an_unbound_variable_is_refused(compiler: FormulaCompiler) -> None:
    with pytest.raises(CompilationError, match="missing"):
        compiler.evaluate(call("rank", var("nosuch")), {"x": PRICES})


def test_a_preprocessing_operator_is_not_available_in_the_compiler(
    compiler: FormulaCompiler,
) -> None:
    """winsorize / zscore / industry_neutralize live only in the pipeline.

    They carry ordering semantics a tree cannot express, so allowing them here
    would let the same run standardize twice with no record of it.
    """
    for op in ("winsorize", "zscore", "industry_neutralize"):
        node = FormulaNode.model_construct(type="call", op=op, args=[var("x")])
        with pytest.raises(CompilationError, match="pipeline"):
            compiler.evaluate(node, {"x": PRICES})


def test_a_non_positive_window_is_refused(compiler: FormulaCompiler) -> None:
    with pytest.raises(CompilationError, match="window"):
        compiler.evaluate(call("rolling_mean", var("x"), window=0), {"x": PRICES})


def test_a_rolling_operator_without_a_window_is_refused(
    compiler: FormulaCompiler,
) -> None:
    with pytest.raises(CompilationError, match="window"):
        compiler.evaluate(call("rolling_std", var("x")), {"x": PRICES})


# --------------------------------------------------------------------------- nesting


def test_nested_expression_evaluates_inside_out(compiler: FormulaCompiler) -> None:
    node = call("rank", call("rolling_return", var("close"), window=2))
    result = compiler.evaluate(node, {"close": PRICES})
    assert result.shape == PRICES.shape
    assert np.isnan(result.iloc[0]).all()
    # Row 2 is the first with a t-2 observation: 600519 returns +20%, 000001 +10%.
    assert result.iloc[2].tolist() == [1.0, 0.5]
