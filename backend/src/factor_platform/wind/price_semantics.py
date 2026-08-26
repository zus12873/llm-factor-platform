"""Price-adjustment ranking and labels for field confirmation.

This layer never rewrites aliases and never auto-confirms a binding. ``收盘价``
still means ``s_dq_close`` in ``wind_aliases.yaml``; for return-like queries we
*add* and *boost* ``s_dq_adjclose`` and leave the unadjusted row visible.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from factor_platform.domain.models import DataRequirement, FieldCandidate

INFERRED_ADJ_NOTE = "动量/收益类默认推荐后复权收盘价，close ≠ adj_close"
UNADJUSTED_NOTE = "未复权收盘价，不等于复权收盘价"

PRICE_ADJUSTMENT_BY_FIELD: dict[str, str] = {
    "s_dq_close": "none",
    "s_dq_open": "none",
    "s_dq_high": "none",
    "s_dq_low": "none",
    "s_dq_adjclose": "backward",
    "s_dq_adjopen": "backward",
    "s_dq_adjhigh": "backward",
    "s_dq_adjlow": "backward",
    "s_dq_adjclose_backward": "forward",
}

_CLOSE_FAMILY_EN = re.compile(
    r"\b(close|momentum|volatility|adjclose|adj_close)\b",
    re.IGNORECASE,
)
_RETURN_EN = re.compile(r"\breturn\b", re.IGNORECASE)
_ROE_RETURN_EN = re.compile(r"return on (equity|assets)", re.IGNORECASE)
_PRICE_RETURN_ZH = re.compile(r"(?<![产])收益率")
_EXPLICIT_NONE_EN = re.compile(r"\b(unadjusted|raw close)\b", re.IGNORECASE)
_EXPLICIT_FORWARD_EN = re.compile(r"\b(forward adj|fwd adj|pre-?adjust)\b", re.IGNORECASE)
_EXPLICIT_BACKWARD_EN = re.compile(
    r"\b(adj close|adjclose|adj_close|backward adj)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PriceIntent:
    """How a requirement should rank close vs adjusted-close candidates."""

    preferred_field: str | None
    explicit: bool
    family: str | None = None
    also_list: tuple[str, ...] = ()


def _haystack(requirement: DataRequirement) -> str:
    return f"{requirement.logical_name} {requirement.meaning}".strip()


def _is_close_family(text: str) -> bool:
    if any(marker in text for marker in ("收盘价", "动量", "波动")):
        return True
    if _PRICE_RETURN_ZH.search(text):
        return True
    if _CLOSE_FAMILY_EN.search(text) or "adj close" in text.lower():
        return True
    return bool(_RETURN_EN.search(text) and not _ROE_RETURN_EN.search(text))


def _is_explicit_none(text: str) -> bool:
    return any(marker in text for marker in ("不复权", "未复权", "原始收盘价")) or bool(
        _EXPLICIT_NONE_EN.search(text)
    )


def _is_explicit_forward(text: str) -> bool:
    return "前复权" in text or bool(_EXPLICIT_FORWARD_EN.search(text))


def _is_explicit_backward(text: str) -> bool:
    return (
        "后复权" in text
        or bool(_EXPLICIT_BACKWARD_EN.search(text))
        or "adj close" in text.lower()
    )


def classify_price_intent(
    requirement: DataRequirement, use_adjusted_price: bool
) -> PriceIntent:
    """Classify close-family intent. Unrelated requirements are left alone."""
    text = _haystack(requirement)
    if not _is_close_family(text):
        return PriceIntent(preferred_field=None, explicit=False)

    if _is_explicit_none(text):
        return PriceIntent(
            preferred_field="s_dq_close",
            explicit=True,
            family="explicit_none",
            also_list=("s_dq_adjclose", "s_dq_adjclose_backward"),
        )
    if _is_explicit_forward(text):
        return PriceIntent(
            preferred_field="s_dq_adjclose_backward",
            explicit=True,
            family="explicit_forward",
            also_list=("s_dq_close",),
        )
    if _is_explicit_backward(text):
        return PriceIntent(
            preferred_field="s_dq_adjclose",
            explicit=True,
            family="explicit_backward",
            also_list=("s_dq_close",),
        )

    if use_adjusted_price:
        return PriceIntent(
            preferred_field="s_dq_adjclose",
            explicit=False,
            family="inferred_return",
            also_list=("s_dq_close",),
        )
    return PriceIntent(
        preferred_field="s_dq_close",
        explicit=False,
        family="inferred_return",
        also_list=("s_dq_adjclose", "s_dq_adjclose_backward"),
    )


def annotate_candidate(candidate: FieldCandidate, intent: PriceIntent) -> FieldCandidate:
    """Label adjustment without changing ``source_tier`` or dropping the row."""
    field = candidate.field.lower()
    adjustment = PRICE_ADJUSTMENT_BY_FIELD.get(field)
    note = _semantic_note(field, intent)
    updates: dict[str, str | None] = {}
    if adjustment is not None:
        updates["price_adjustment"] = adjustment
    if note is not None:
        updates["semantic_note"] = note
    if not updates:
        return candidate
    return candidate.model_copy(update=updates)


def _semantic_note(field: str, intent: PriceIntent) -> str | None:
    if intent.family is None:
        return None
    if field == "s_dq_close":
        if intent.family == "inferred_return":
            return UNADJUSTED_NOTE
        return "未复权"
    if field == "s_dq_adjclose" and intent.family == "inferred_return":
        return INFERRED_ADJ_NOTE
    return None


def apply_price_semantics(
    candidates: Sequence[FieldCandidate],
    requirement: DataRequirement,
    use_adjusted_price: bool = True,
    *,
    inject: Callable[[str], FieldCandidate | None] | None = None,
    limit: int | None = None,
) -> list[FieldCandidate]:
    """Rerank and label candidates. Missing preferred/also-listed rows may be added."""
    intent = classify_price_intent(requirement, use_adjusted_price)
    merged = list(candidates)
    seen: set[tuple[str, str]] = {(row.table, row.field) for row in merged}

    if inject is not None and intent.preferred_field is not None:
        for field in (intent.preferred_field, *intent.also_list):
            if any(row.field == field for row in merged):
                continue
            extra = inject(field)
            if extra is None:
                continue
            key = (extra.table, extra.field)
            if key in seen:
                continue
            seen.add(key)
            merged.append(extra)

    labelled = [annotate_candidate(row, intent) for row in merged]
    if intent.preferred_field is not None:
        preferred = [row for row in labelled if row.field == intent.preferred_field]
        rest = [row for row in labelled if row.field != intent.preferred_field]
        labelled = preferred + rest
    if limit is not None:
        labelled = labelled[:limit]
    return labelled


__all__ = [
    "INFERRED_ADJ_NOTE",
    "PRICE_ADJUSTMENT_BY_FIELD",
    "PriceIntent",
    "UNADJUSTED_NOTE",
    "annotate_candidate",
    "apply_price_semantics",
    "classify_price_intent",
]
