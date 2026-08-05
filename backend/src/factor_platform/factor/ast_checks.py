"""Legality checks over a whole formula tree.

:class:`FormulaNode` guarantees each node is *shaped* correctly. That is not the
same as the tree being sane. These checks answer the next three questions before
anything is planned or executed:

* **Binding** — does every referenced variable exist, exactly once, under a name
  the rest of the pipeline can carry?
* **Complexity** — is the tree within limits the compiler and the Worker can
  honour, and are rolling windows plausible?
* **Numeric legality** — can a literal poison the computation with a non-finite
  value?

Binding and numeric problems are blocking; a declared-but-unused variable is
merely suspicious, so it warns.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Final

from factor_platform.domain.formula import ROLLING_OPERATORS, FormulaNode
from factor_platform.domain.models import (
    DataRequirement,
    ValidationFinding,
    ValidationReport,
    ValidationSeverity,
)

# Limits are deliberately generous: they exist to stop pathological trees, not to
# second-guess research. A 2520-day window is ten trading years.
MAX_NODES: Final = 200
MAX_DEPTH: Final = 20
MAX_PARAMS: Final = 8
MAX_WINDOW: Final = 2520
MIN_WINDOW: Final = 1

_VARIABLE_NAME: Final = re.compile(r"^[a-z][a-z0-9_]*$")


def _error(code: str, message: str, **evidence: object) -> ValidationFinding:
    return ValidationFinding(
        severity=ValidationSeverity.ERROR, code=code, message=message, evidence=dict(evidence)
    )


def _warning(code: str, message: str, **evidence: object) -> ValidationFinding:
    return ValidationFinding(
        severity=ValidationSeverity.WARNING, code=code, message=message, evidence=dict(evidence)
    )


def _walk(node: FormulaNode, depth: int = 1) -> list[tuple[FormulaNode, int]]:
    found = [(node, depth)]
    for arg in node.args:
        found.extend(_walk(arg, depth + 1))
    return found


def check_ast(
    node: FormulaNode, variables: Sequence[DataRequirement]
) -> ValidationReport:
    """Audit ``node`` against the declared ``variables``.

    Returns a report rather than raising: the caller decides whether a warning is
    tolerable, while any ERROR finding must block progress.
    """
    findings: list[ValidationFinding] = []
    nodes = _walk(node)

    findings.extend(_check_binding(nodes, variables))
    findings.extend(_check_complexity(nodes))
    findings.extend(_check_numeric(nodes))

    return ValidationReport(findings=findings)


def check_factor_spec(spec: object) -> ValidationReport:
    """Convenience wrapper for a :class:`FactorSpec`-shaped object."""
    return check_ast(spec.formula_ast, spec.variables)  # type: ignore[attr-defined]


# --------------------------------------------------------------------- binding


def _declared_names(variables: Sequence[DataRequirement]) -> list[str]:
    return [variable.logical_name for variable in variables]


def _check_binding(
    nodes: list[tuple[FormulaNode, int]], variables: Sequence[DataRequirement]
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    declared = _declared_names(variables)

    repeated = sorted(name for name, count in Counter(declared).items() if count > 1)
    if repeated:
        findings.append(
            _error(
                "duplicate_variable",
                f"变量逻辑名重复：{repeated}",
                names=repeated,
            )
        )

    malformed = sorted({name for name in declared if not _VARIABLE_NAME.match(name)})
    if malformed:
        findings.append(
            _error(
                "invalid_variable_name",
                f"变量名不符合命名规范（小写字母开头、仅含小写字母数字下划线）：{malformed}",
                names=malformed,
            )
        )

    referenced = {
        str(item.name) for item, _ in nodes if item.type == "variable" and item.name
    }
    unbound = sorted(referenced - set(declared))
    if unbound:
        findings.append(
            _error(
                "unbound_variable",
                f"公式引用了未声明的变量：{unbound}",
                names=unbound,
            )
        )

    unused = sorted(set(declared) - referenced)
    if unused:
        findings.append(
            _warning(
                "unused_variable",
                f"声明了但公式未使用的变量：{unused}",
                names=unused,
            )
        )
    return findings


# ------------------------------------------------------------------ complexity


def _check_complexity(nodes: list[tuple[FormulaNode, int]]) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []

    if len(nodes) > MAX_NODES:
        findings.append(
            _error(
                "node_count_exceeded",
                f"公式节点数 {len(nodes)} 超过上限 {MAX_NODES}",
                actual=len(nodes),
                limit=MAX_NODES,
            )
        )

    deepest = max((depth for _, depth in nodes), default=0)
    if deepest > MAX_DEPTH:
        findings.append(
            _error(
                "depth_exceeded",
                f"公式嵌套深度 {deepest} 超过上限 {MAX_DEPTH}",
                actual=deepest,
                limit=MAX_DEPTH,
            )
        )

    for item, _ in nodes:
        if item.type != "call":
            continue
        if len(item.params) > MAX_PARAMS:
            findings.append(
                _error(
                    "param_count_exceeded",
                    f"算子 {item.op} 的参数数量 {len(item.params)} 超过上限 {MAX_PARAMS}",
                    op=item.op,
                )
            )
        findings.extend(_check_window(item))
    return findings


def _check_window(item: FormulaNode) -> list[ValidationFinding]:
    if item.op not in ROLLING_OPERATORS:
        return []

    if "window" not in item.params:
        return [
            _error(
                "missing_window",
                f"滚动算子 {item.op} 缺少 window 参数",
                op=item.op,
            )
        ]

    window = item.params["window"]
    if isinstance(window, str) or isinstance(window, float) and window != int(window):
        return [
            _error(
                "window_out_of_range",
                f"算子 {item.op} 的 window 必须是正整数，实际为 {window!r}",
                op=item.op,
                window=window,
            )
        ]

    value = int(window)
    if value < MIN_WINDOW or value > MAX_WINDOW:
        return [
            _error(
                "window_out_of_range",
                f"算子 {item.op} 的 window={value} 超出允许区间 [{MIN_WINDOW}, {MAX_WINDOW}]",
                op=item.op,
                window=value,
            )
        ]
    return []


# -------------------------------------------------------------- numeric legality


def _check_numeric(nodes: list[tuple[FormulaNode, int]]) -> list[ValidationFinding]:
    offending = [
        item.value
        for item, _ in nodes
        if item.type == "literal" and item.value is not None and not math.isfinite(item.value)
    ]
    if not offending:
        return []
    return [
        _error(
            "non_finite_literal",
            f"公式中存在非有限字面量：{offending}",
            values=[repr(value) for value in offending],
        )
    ]


__all__ = [
    "MAX_DEPTH",
    "MAX_NODES",
    "MAX_PARAMS",
    "MAX_WINDOW",
    "MIN_WINDOW",
    "check_ast",
    "check_factor_spec",
]
