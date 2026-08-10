"""Render a readable ``factor.py`` from a manifest, for the user — not for us.

This file is a deliverable. A researcher reads it to see what was computed, copies
it into their own environment, and reproduces the result without this platform.
Nothing internal ever executes it.

That last sentence is the entire security argument. When generated Python *was*
the execution path, it needed a source-level AST whitelist, dynamic-import checks
and a byte-equality proof against the displayed code. Moving execution to the
manifest did not make the export safe by adding controls; it made the controls
unnecessary by removing the export from the path where they were needed.

The export is rendered deterministically from the same manifest that ran, and it
embeds that manifest's hash so a reviewer holding only the file can tell which run
it describes.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel

from factor_platform.execution.manifest import Manifest


class ExportedProgram(BaseModel):
    """A rendered ``factor.py`` and its own content hash."""

    source: str
    sha256: str
    manifest_sha256: str


class CodeExporter:
    """Renders a manifest as human-readable, runnable Python."""

    def render(self, manifest: Manifest) -> ExportedProgram:
        source = _TEMPLATE.format(
            manifest_sha256=manifest.sha256,
            factor_name=manifest.factor_spec.factor_name,
            canonical_formula=manifest.factor_spec.canonical_formula,
            hypothesis=manifest.factor_spec.hypothesis or "(未填写)",
            universe=manifest.factor_spec.universe,
            observation_time=manifest.time_convention.observation_time.value,
            signal_date=manifest.time_convention.signal_date,
            trade_date=manifest.time_convention.trade_date,
            execution_price=manifest.time_convention.execution_price.value,
            bindings=_render_bindings(manifest),
            preprocessing=_render_pipeline(manifest),
            steps=_render_steps(manifest),
            formula_ast=manifest.factor_spec.formula_ast.model_dump_json(
                exclude_none=True
            ),
        )
        return ExportedProgram(
            source=source,
            sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            manifest_sha256=manifest.sha256,
        )


def _render_bindings(manifest: Manifest) -> str:
    if not manifest.field_selections:
        return "#   (无已确认字段)"
    return "\n".join(
        f"#   {s.logical_name} -> {s.table}.{s.field}"
        + (f"  [公告日 {s.announcement_date_field}]" if s.announcement_date_field else "")
        for s in manifest.field_selections
    )


def _render_pipeline(manifest: Manifest) -> str:
    steps = manifest.preprocessing.ordered_steps()
    if not steps:
        return "#   (无预处理)"
    return "\n".join(
        f"#   {s.order}. {s.operation.value} on {s.target.value}" for s in steps
    )


def _render_steps(manifest: Manifest) -> str:
    return "\n".join(
        f"#   {index}. {step.tool}" + (f"  — {step.purpose}" if step.purpose else "")
        for index, step in enumerate(manifest.execution_plan.steps, start=1)
    )


_TEMPLATE = '''"""{factor_name} — 因子导出

本文件由平台从 manifest 确定性渲染，供人阅读、复制与在你自己的环境中复现。

重要：**这不是平台实际执行的对象**。平台执行的是下面这份签名 manifest：

    manifest sha256 = {manifest_sha256}

两者由同一份 manifest 渲染而来，但本文件不进入内部执行路径。若你修改本文件，
改动不会影响平台的任何一次运行。

--------------------------------------------------------------------------------
假设
{hypothesis}

正式公式（用户确认的即为此式，由 AST 确定性渲染）
    {canonical_formula}

股票池
    {universe}

时间口径
#   观测时点   {observation_time}
#   信号日     {signal_date}
#   交易日     {trade_date}
#   执行价     {execution_price}

已确认字段绑定
{bindings}

预处理流水线（按序执行，顺序影响结果）
{preprocessing}

取数计划
{steps}
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import json

import pandas as pd

# 与平台执行的完全相同的公式结构。
FORMULA_AST = json.loads(
    r"""{formula_ast}"""
)

MANIFEST_SHA256 = "{manifest_sha256}"


def compute(variables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """用 variables 计算因子值。

    variables 的每个键对应上面「已确认字段绑定」中的一个逻辑变量，值为
    index 为日期、columns 为证券代码的 DataFrame。

    平台内部用 factor_platform.factor.compiler.FormulaCompiler 求值同一份 AST；
    若你已安装该包，可直接：

        from factor_platform.factor.compiler import FormulaCompiler
        from factor_platform.domain.formula import FormulaNode

        node = FormulaNode.model_validate(FORMULA_AST)
        factor = FormulaCompiler().evaluate(node, variables)

    否则请按上面的正式公式自行实现，并注意：滚动窗口未满时应为 NaN，
    不得用不足的历史数据出值。
    """
    raise NotImplementedError(
        "请安装 factor_platform 后使用 FormulaCompiler，或按正式公式自行实现"
    )
'''


__all__ = ["CodeExporter", "ExportedProgram"]
