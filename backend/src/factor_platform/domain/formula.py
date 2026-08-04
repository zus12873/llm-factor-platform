"""Formula DSL abstract syntax tree.

The AST is the only machine-executable representation of a factor. ``formula_text``
is display-only and never compiled. A :class:`FormulaNode` is a single discriminated
model: exactly one of the variable / literal / call shapes is valid, enforced by a
model validator so malformed nodes can never reach the compiler.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Operators the deterministic compiler knows how to evaluate (Task 12).
# Adding an operator here is the only way to make it executable.
FormulaOperator = Literal[
    "add",
    "subtract",
    "multiply",
    "divide",
    "negative",
    "log",
    "rank",
    "zscore",
    "winsorize",
    "rolling_return",
    "rolling_std",
    "rolling_mean",
    "fillna",
    "industry_neutralize",
]


class FormulaNode(BaseModel):
    """A single node in the factor formula AST.

    Shapes (mutually exclusive, enforced):
      * ``variable`` -- references a confirmed data variable by ``name``;
      * ``literal``  -- a numeric constant ``value``;
      * ``call``     -- applies a registered ``op`` to ``args`` with optional ``params``.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["variable", "literal", "call"]
    name: str | None = None
    value: float | None = None
    op: FormulaOperator | None = None
    args: list[FormulaNode] = Field(default_factory=list)
    params: dict[str, float | int | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _enforce_exclusive_shape(self) -> FormulaNode:
        if self.type == "variable":
            if self.name is None or self._has_call_or_literal_fields():
                raise ValueError("variable node requires only 'name'")
        elif self.type == "literal":
            if self.value is None or self._has_variable_or_call_fields():
                raise ValueError("literal node requires only 'value'")
        else:  # call
            if self.op is None or self.name is not None or self.value is not None:
                raise ValueError("call node requires 'op' and must not carry name/value")
        return self

    def _has_call_or_literal_fields(self) -> bool:
        return self.value is not None or self.op is not None or bool(self.args) or bool(self.params)

    def _has_variable_or_call_fields(self) -> bool:
        return self.name is not None or self.op is not None or bool(self.args) or bool(self.params)
