"""Token-usage accounting shared across providers."""

from __future__ import annotations

from factor_platform.llm.base import UsageRecord


class LLMUsageSink:
    """Collects :class:`UsageRecord` entries for admin dashboards and cost tracking."""

    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    def record(self, record: UsageRecord) -> None:
        self.records.append(record)

    def summary(self) -> dict[str, int | float]:
        calls = len(self.records)
        failures = sum(1 for record in self.records if not record.success)
        total_tokens = sum(record.total_tokens for record in self.records if record.total_tokens)
        total_cost = sum(record.cost for record in self.records if record.cost)
        failure_rate = failures / calls if calls else 0.0
        return {
            "calls": calls,
            "failures": failures,
            "failure_rate": failure_rate,
            "total_tokens": total_tokens,
            "total_cost": total_cost,
        }


__all__ = ["LLMUsageSink"]
