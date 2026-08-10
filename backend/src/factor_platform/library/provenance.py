"""The reproduction record: everything needed to get the same numbers again.

A program hash plus a result hash is not enough, and the reason is specific to
financial data: **Wind restates financials.** A company revises last quarter's
earnings, Wind updates the row, and the identical program run three months later
produces different values. Both runs are correct. Without a record of what the
data looked like at query time, there is no way to tell that apart from a bug —
and the person investigating starts by suspecting the code.

So the record pins three things:

* **What was read** — the input artifact hash, the tables and fields, the query
  parameters, the row count and the non-null ratio. If a rerun disagrees, these
  say whether the data moved.
* **When it was read** — the query timestamp and the data date range. Restatement
  is a function of time, so the timestamp is the thing that explains a diff.
* **What read it** — every version that could change the answer: the spec, the
  plan, the preprocessing, the time convention, the field metadata, the metric
  definitions, the adapter, the code commit and the runtime.

The last list is deliberately long. Each entry is something that has silently
changed a factor's value at least once in systems like this, and a version nobody
recorded is a version nobody can rule out.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class InputProvenance(BaseModel):
    """What was read, from where, and what it looked like at the time."""

    input_artifact_sha256: str
    query_timestamp: str
    source_database: str
    source_table: str
    source_fields: list[str] = Field(default_factory=list)
    query_parameters: dict[str, Any] = Field(default_factory=dict)
    data_date_range: tuple[str, str] | None = None
    row_count: int = 0
    input_schema: dict[str, str] = Field(default_factory=dict)
    #: Recorded so a rerun with more (or fewer) nulls is visible as a data change
    #: rather than being read as a code regression.
    input_non_null_ratio: float = 0.0
    query_plan_sha256: str = ""


class ComponentVersions(BaseModel):
    """Every version that can change the answer.

    Long on purpose. Each of these has silently altered a factor's value in
    systems like this, and a version nobody recorded is one nobody can rule out
    when a rerun disagrees.
    """

    factor_spec_version: int = 1
    execution_plan_version: int = 1
    preprocessing_version: int = 1
    time_convention_version: int = 1
    field_metadata_version: int = 1
    metric_definition_version: int = 1
    wind_adapter_version: str = "0.1.0"
    code_commit: str = ""
    runtime_version: str = ""


class ProvenanceRecord(BaseModel):
    """The complete reproduction record for one published factor version."""

    schema_version: int = 1
    manifest_sha256: str
    result_sha256: str
    inputs: list[InputProvenance] = Field(default_factory=list)
    versions: ComponentVersions = Field(default_factory=ComponentVersions)

    def explains_difference_from(self, other: ProvenanceRecord) -> list[str]:
        """List what differs between two records.

        The point of the record: when a rerun disagrees, this says whether the
        code moved, the data moved, or a definition moved — before anyone starts
        reading diffs.
        """
        reasons: list[str] = []

        if self.manifest_sha256 != other.manifest_sha256:
            reasons.append("manifest 不同：公式、计划或预处理发生了变化")

        mine = {i.input_artifact_sha256 for i in self.inputs}
        theirs = {i.input_artifact_sha256 for i in other.inputs}
        if mine != theirs:
            reasons.append(
                "输入工件哈希不同：源数据已被修订（Wind 财务数据会追溯调整），"
                "或取数区间不同"
            )

        my_versions = self.versions.model_dump()
        their_versions = other.versions.model_dump()
        changed = [
            key for key, value in my_versions.items() if their_versions.get(key) != value
        ]
        if changed:
            reasons.append(f"组件版本不同：{', '.join(sorted(changed))}")

        if not reasons and self.result_sha256 != other.result_sha256:
            # Everything recorded matches and the numbers still differ, which
            # means something that changes the answer is not being recorded.
            reasons.append(
                "所有已记录项一致但结果不同——存在未被记录的可变因素，"
                "这本身是需要修复的缺陷"
            )
        return reasons


__all__ = ["ComponentVersions", "InputProvenance", "ProvenanceRecord"]
