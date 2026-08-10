"""Formula compiler: a confirmed AST becomes a DataFrame, via explicit functions only.

Eleven operators, each a named Python function, dispatched through a dict. No
``eval``, no ``exec``, no dynamic import — there is no path by which model output
becomes executable code, which is the whole reason the model emits an AST instead
of Python in the first place.

Two axis conventions are fixed here and must not be inferred per call:

* ``rank`` runs **across securities within a date**. Ranking down the time axis
  would score each stock against its own history rather than against its peers,
  and the output would still look like a factor.
* rolling operators run **along time within a security**, with
  ``min_periods=window``. pandas defaults to ``min_periods=1``, which returns a
  20-day momentum computed from three days as though it were a 20-day figure —
  the early part of every backtest silently becomes a different factor.

``window`` means two different things, and the difference is deliberate. For the
rolling *aggregates* it is an observation count: ``rolling_mean(x, 20)`` averages
twenty values. For ``rolling_return`` it is a **lag**:
``rolling_return(close, 20)`` is ``close[t] / close[t-20] - 1``, because that is
what "20-day momentum" means to the person who asked for it. Treating it as an
observation count would silently span nineteen periods instead of twenty.

The three preprocessing operators are deliberately absent. They carry ordering
semantics a tree cannot express, so they live in the pipeline executor where the
order is declared and recorded.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

import numpy as np
import pandas as pd

from factor_platform.domain.errors import DomainError
from factor_platform.domain.formula import ROLLING_OPERATORS, FormulaNode

#: Operators that were moved out of the AST in Task 2.5. Named so the error can
#: say where they went instead of "unknown operator".
_PIPELINE_ONLY: Final[frozenset[str]] = frozenset(
    {"winsorize", "zscore", "industry_neutralize"}
)

#: Windows longer than ten years of trading days are almost certainly a units
#: mistake (calendar days for trading days) rather than an intention.
MAX_WINDOW: Final = 2520


class CompilationError(DomainError):
    """Raised when a formula cannot be evaluated as written."""


class FormulaCompiler:
    """Evaluates a :class:`FormulaNode` against bound variable frames."""

    def evaluate(
        self, node: FormulaNode, variables: Mapping[str, pd.DataFrame]
    ) -> pd.DataFrame:
        """Return the frame produced by ``node``.

        ``variables`` maps each logical name in the formula to a DataFrame
        indexed by date with one column per security.
        """
        if node.type == "variable":
            return self._variable(node, variables)
        if node.type == "literal":
            return self._literal(node, variables)
        return self._call(node, variables)

    # ------------------------------------------------------------------ leaves

    @staticmethod
    def _variable(
        node: FormulaNode, variables: Mapping[str, pd.DataFrame]
    ) -> pd.DataFrame:
        name = node.name or ""
        if name not in variables:
            raise CompilationError(
                f"missing binding for variable {name!r}; "
                f"bound variables are {sorted(variables)}"
            )
        return variables[name]

    def _literal(
        self, node: FormulaNode, variables: Mapping[str, pd.DataFrame]
    ) -> pd.DataFrame:
        """Broadcast a scalar over the shape of the bound data."""
        if not variables:
            raise CompilationError("a literal cannot be evaluated without any variable")
        template = next(iter(variables.values()))
        return pd.DataFrame(
            float(node.value if node.value is not None else np.nan),
            index=template.index,
            columns=template.columns,
        )

    # ------------------------------------------------------------------ calls

    def _call(
        self, node: FormulaNode, variables: Mapping[str, pd.DataFrame]
    ) -> pd.DataFrame:
        op = node.op or ""
        if op in _PIPELINE_ONLY:
            raise CompilationError(
                f"{op!r} is not a formula operator; it runs in the preprocessing "
                "pipeline, where its order relative to the other steps is declared"
            )
        handler = _OPERATORS.get(op)
        if handler is None:
            raise CompilationError(f"unknown operator {op!r}")

        operands = [self.evaluate(arg, variables) for arg in node.args or []]
        params = dict(node.params or {})
        if op in ROLLING_OPERATORS:
            params["window"] = _validated_window(op, params.get("window"))
        return handler(operands, params)


# --------------------------------------------------------------------------- operators


def _binary(
    operands: list[pd.DataFrame], name: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if len(operands) != 2:
        raise CompilationError(f"{name} takes exactly two operands, got {len(operands)}")
    # Align on both axes so misordered columns are never combined positionally.
    left, right = operands[0].align(operands[1], join="outer")
    return left, right


def _unary(operands: list[pd.DataFrame], name: str) -> pd.DataFrame:
    if len(operands) != 1:
        raise CompilationError(f"{name} takes exactly one operand, got {len(operands)}")
    return operands[0]


def _add(operands: list[pd.DataFrame], _: dict[str, Any]) -> pd.DataFrame:
    left, right = _binary(operands, "add")
    return left + right


def _subtract(operands: list[pd.DataFrame], _: dict[str, Any]) -> pd.DataFrame:
    left, right = _binary(operands, "subtract")
    return left - right


def _multiply(operands: list[pd.DataFrame], _: dict[str, Any]) -> pd.DataFrame:
    left, right = _binary(operands, "multiply")
    return left * right


def _divide(operands: list[pd.DataFrame], _: dict[str, Any]) -> pd.DataFrame:
    """Division with infinities collapsed to NaN.

    An infinite value survives ranking and drags every other security in that
    day's cross-section with it, so a single zero denominator would corrupt the
    whole date rather than one cell.
    """
    left, right = _binary(operands, "divide")
    with np.errstate(divide="ignore", invalid="ignore"):
        result = left / right
    return result.replace([np.inf, -np.inf], np.nan)


def _negative(operands: list[pd.DataFrame], _: dict[str, Any]) -> pd.DataFrame:
    return -_unary(operands, "negative")


def _log(operands: list[pd.DataFrame], _: dict[str, Any]) -> pd.DataFrame:
    """Natural log; non-positive inputs become NaN rather than -inf or an error."""
    values = _unary(operands, "log")
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.log(values.where(values > 0))
    return pd.DataFrame(result, index=values.index, columns=values.columns)


def _rank(operands: list[pd.DataFrame], _: dict[str, Any]) -> pd.DataFrame:
    """Percentile rank across securities within each date."""
    return _unary(operands, "rank").rank(axis=1, pct=True)


def _rolling_mean(operands: list[pd.DataFrame], params: dict[str, Any]) -> pd.DataFrame:
    window = int(params["window"])
    return _unary(operands, "rolling_mean").rolling(window, min_periods=window).mean()


def _rolling_std(operands: list[pd.DataFrame], params: dict[str, Any]) -> pd.DataFrame:
    window = int(params["window"])
    return _unary(operands, "rolling_std").rolling(window, min_periods=window).std()


def _rolling_return(operands: list[pd.DataFrame], params: dict[str, Any]) -> pd.DataFrame:
    """Simple return over ``window`` periods, using only past observations."""
    window = int(params["window"])
    return _unary(operands, "rolling_return").pct_change(periods=window, fill_method=None)


def _fillna(operands: list[pd.DataFrame], params: dict[str, Any]) -> pd.DataFrame:
    return _unary(operands, "fillna").fillna(float(params.get("value", 0.0)))


_OPERATORS: Final[
    dict[str, Callable[[list[pd.DataFrame], dict[str, Any]], pd.DataFrame]]
] = {
    "add": _add,
    "subtract": _subtract,
    "multiply": _multiply,
    "divide": _divide,
    "negative": _negative,
    "log": _log,
    "rank": _rank,
    "rolling_mean": _rolling_mean,
    "rolling_std": _rolling_std,
    "rolling_return": _rolling_return,
    "fillna": _fillna,
}


def _validated_window(op: str, raw: Any) -> int:
    if raw is None:
        raise CompilationError(f"{op} requires a window parameter")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise CompilationError(f"{op} window must be a number, got {raw!r}")
    window = int(raw)
    if window != raw or window < 1:
        raise CompilationError(f"{op} window must be a positive integer, got {raw!r}")
    if window > MAX_WINDOW:
        raise CompilationError(
            f"{op} window {window} exceeds {MAX_WINDOW} trading days; "
            "this is usually calendar days mistaken for trading days"
        )
    return window


__all__ = ["MAX_WINDOW", "CompilationError", "FormulaCompiler"]
