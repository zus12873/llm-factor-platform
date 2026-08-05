"""Render a formula AST into the canonical formula the user confirms.

The model produces only ``formula_ast``. The string a user reads and signs off on
is produced *here*, from that same tree. That is the whole point: if the model
also authored the display text, a user could confirm formula A while the system
executed formula B, and the confirmation step would be theatre.

Rendering is therefore required to be deterministic and total — the same tree
always yields the same string, and every structurally valid tree is renderable.
"""

from __future__ import annotations

from factor_platform.domain.formula import FormulaNode


def _render_number(value: float) -> str:
    """Render a numeric literal without a spurious decimal tail.

    ``20.0`` becomes ``20`` so a window written as an int and one written as a
    float render identically; genuine fractions keep their digits.
    """
    if value == int(value):
        return str(int(value))
    return repr(value)


def _render_param_value(value: float | int | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return _render_number(float(value))
    return str(value)


def render_canonical_formula(node: FormulaNode) -> str:
    """Return the canonical text for ``node``.

    Parameters are emitted after positional arguments and sorted by name, so two
    trees that differ only in dict insertion order render identically.
    """
    if node.type == "variable":
        # Shape validation guarantees name is present for variable nodes.
        return str(node.name)

    if node.type == "literal":
        return _render_number(float(node.value or 0.0))

    parts = [render_canonical_formula(arg) for arg in node.args]
    parts.extend(
        f"{key}={_render_param_value(node.params[key])}" for key in sorted(node.params)
    )
    return f"{node.op}({', '.join(parts)})"


__all__ = ["render_canonical_formula"]
