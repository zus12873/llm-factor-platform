"""Tests for trust boundary B4: internal data leaving for an external model.

Every other boundary in this system protects the platform from what comes *in*.
B4 is the only one that protects the data from what goes *out*, and it is the
only boundary whose failure is silent: a prompt carrying a Wind price table to a
third-party API succeeds, returns a good answer, and leaves no trace that
licensed data was redistributed.

The rule is default-deny by category. Field *names* are metadata and may leave;
field *values* are data and may not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

import factor_platform
from factor_platform.llm.base import ChatMessage
from factor_platform.llm.data_boundary import (
    GuardedProvider,
    LocalOnlyModeError,
    OutboundBlockedError,
    OutboundCategory,
    OutboundFilter,
)


class _Answer(BaseModel):
    answer: str


# --------------------------------------------------------------------------- blocked


def test_wind_price_rows_are_blocked() -> None:
    violation = OutboundFilter().check(
        {
            "rows": [
                {"s_info_windcode": "600519.SH", "trade_dt": "20240102", "s_dq_close": 1688.0},
                {"s_info_windcode": "000001.SZ", "trade_dt": "20240102", "s_dq_close": 9.87},
            ]
        }
    )
    assert violation is not None
    assert violation.category is OutboundCategory.WIND_RAW_DATA


def test_a_single_data_row_is_enough_to_block() -> None:
    violation = OutboundFilter().check(
        {"sample": {"s_info_windcode": "600519.SH", "s_dq_close": 1688.0}}
    )
    assert violation is not None
    assert violation.category is OutboundCategory.WIND_RAW_DATA


def test_connection_string_is_blocked() -> None:
    violation = OutboundFilter().check(
        {"context": "engine = create_engine('mysql+pymysql://u:p@10.0.0.5:3306/wind')"}
    )
    assert violation is not None
    assert violation.category is OutboundCategory.CONNECTION_STRING


def test_api_key_shaped_string_is_blocked() -> None:
    violation = OutboundFilter().check({"note": "use sk-abcdefghijklmnopqrstuvwxyz012345"})
    assert violation is not None
    assert violation.category is OutboundCategory.SECRET


def test_private_key_block_is_blocked() -> None:
    violation = OutboundFilter().check(
        {"attachment": "-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\n"}
    )
    assert violation is not None
    assert violation.category is OutboundCategory.SECRET


def test_a_credential_bearing_key_is_blocked_whatever_its_value() -> None:
    """Naming a key ``wind_password`` is enough; the value is never inspected."""
    violation = OutboundFilter().check({"wind_password": "anything"})
    assert violation is not None
    assert violation.category is OutboundCategory.SECRET


def test_generated_source_is_blocked() -> None:
    violation = OutboundFilter().check(
        {"generated_code": "import pandas as pd\n\ndef factor(df):\n    return df\n"}
    )
    assert violation is not None
    assert violation.category is OutboundCategory.INTERNAL_CODE


def test_full_report_body_is_blocked_by_default() -> None:
    violation = OutboundFilter().check({"report_text": "研报正文" * 500})
    assert violation is not None
    assert violation.category is OutboundCategory.FULL_REPORT_BODY


def test_violation_names_the_location_but_never_the_value() -> None:
    """An audit trail that quotes the leak is itself a leak."""
    secret = "sk-abcdefghijklmnopqrstuvwxyz012345"
    violation = OutboundFilter().check({"outer": {"inner": {"note": secret}}})
    assert violation is not None
    assert violation.location == "outer.inner.note"
    assert secret not in violation.detail
    assert secret not in str(violation)


def test_nested_payloads_are_scanned_to_the_leaves() -> None:
    violation = OutboundFilter().check(
        {"messages": [{"role": "user", "content": {"deep": {"wind_user": "reader"}}}]}
    )
    assert violation is not None
    assert violation.category is OutboundCategory.SECRET


# --------------------------------------------------------------------------- allowed


def test_field_metadata_is_allowed() -> None:
    """Field *names* describe the schema; they are not the licensed data."""
    assert (
        OutboundFilter().check(
            {
                "candidates": [
                    {
                        "table": "ashareeodprices",
                        "field": "s_dq_close",
                        "description": "收盘价",
                    },
                    {
                        "table": "ashareeodprices",
                        "field": "s_dq_adjclose",
                        "description": "后复权收盘价",
                    },
                ]
            }
        )
        is None
    )


def test_formula_structure_is_allowed() -> None:
    assert (
        OutboundFilter().check(
            {
                "formula_ast": {
                    "type": "call",
                    "op": "rank",
                    "args": [{"type": "variable", "name": "roe_ttm"}],
                },
                "canonical_formula": "rank(roe_ttm)",
            }
        )
        is None
    )


def test_user_research_idea_is_allowed() -> None:
    assert (
        OutboundFilter().check({"research_idea": "ROE 高的股票未来收益更好"}) is None
    )


def test_error_summary_is_allowed() -> None:
    assert (
        OutboundFilter().check(
            {"error_summary": "empty result for 2024-01-02, 0 rows returned"}
        )
        is None
    )


def test_a_window_length_is_not_mistaken_for_market_data() -> None:
    """Numbers alone are not data rows; the key must look like a Wind field."""
    assert OutboundFilter().check({"params": {"window": 20, "min_periods": 15}}) is None


# --------------------------------------------------------------------------- report excerpts


def test_report_excerpt_is_blocked_unless_explicitly_enabled() -> None:
    violation = OutboundFilter().check({"report_excerpt": "本文提出动量因子。"})
    assert violation is not None
    assert violation.category is OutboundCategory.FULL_REPORT_BODY


def test_report_excerpt_within_the_limit_is_allowed_when_enabled() -> None:
    boundary = OutboundFilter(allow_report_excerpt=True, max_excerpt_chars=100)
    assert boundary.check({"report_excerpt": "本文提出动量因子。" * 5}) is None


def test_report_excerpt_over_the_limit_is_blocked_even_when_enabled() -> None:
    boundary = OutboundFilter(allow_report_excerpt=True, max_excerpt_chars=50)
    violation = boundary.check({"report_excerpt": "本文提出动量因子。" * 50})
    assert violation is not None
    assert violation.category is OutboundCategory.FULL_REPORT_BODY


# --------------------------------------------------------------------------- local-only mode


async def test_local_only_mode_refuses_every_provider_call(router) -> None:
    router.local_only_mode = True
    with pytest.raises(LocalOnlyModeError):
        await router.active_provider()


async def test_local_only_mode_off_still_routes_normally(router, coding) -> None:
    assert await router.active_provider() is coding


# --------------------------------------------------------------------------- guarded provider


async def test_a_guarded_call_carrying_a_secret_never_reaches_the_provider(
    mock_provider,
) -> None:
    """The provider must not be given the chance to transmit it."""
    guarded = GuardedProvider(mock_provider, OutboundFilter())
    mock_provider.enqueue_content('{"answer": "ok"}')

    with pytest.raises(OutboundBlockedError):
        await guarded.structured_chat(
            [ChatMessage(role="user", content="key is sk-abcdefghijklmnopqrstuvwxyz012345")],
            _Answer,
        )
    # The queued response is untouched: the call never happened.
    assert await mock_provider.structured_chat([], _Answer) == _Answer(answer="ok")


async def test_a_serialized_wind_row_in_a_prompt_is_blocked(mock_provider) -> None:
    """Rendering the table to text before sending must not slip past the filter."""
    guarded = GuardedProvider(mock_provider, OutboundFilter())

    with pytest.raises(OutboundBlockedError) as excinfo:
        await guarded.structured_chat(
            [ChatMessage(role="user", content='data: {"s_dq_close": 1688.0}')],
            _Answer,
        )
    assert excinfo.value.violation.category is OutboundCategory.WIND_RAW_DATA


async def test_a_clean_guarded_call_passes_through(mock_provider) -> None:
    guarded = GuardedProvider(mock_provider, OutboundFilter())
    mock_provider.enqueue_content('{"answer": "rank(roe_ttm)"}')

    result = await guarded.structured_chat(
        [ChatMessage(role="user", content="ROE 高的股票未来收益更好")], _Answer
    )
    assert result.answer == "rank(roe_ttm)"


# --------------------------------------------------------------------------- audit


async def test_audit_record_measures_the_prompt_without_storing_it(
    mock_provider, usage_sink
) -> None:
    guarded = GuardedProvider(mock_provider, OutboundFilter())
    mock_provider.enqueue_content('{"answer": "ok"}')
    await guarded.structured_chat(
        [ChatMessage(role="user", content="ROE 高的股票未来收益更好")], _Answer
    )

    record = usage_sink.records[-1]
    assert record.text_length > 0
    # Length and shape are auditable; the body is not retained anywhere.
    assert not hasattr(record, "prompt_body")
    assert "ROE" not in record.model_dump_json()


async def test_a_blocked_call_is_audited_with_its_reason(
    mock_provider, usage_sink
) -> None:
    """A refusal is the most important thing to audit, and the easiest to lose."""
    guarded = GuardedProvider(mock_provider, OutboundFilter())
    with pytest.raises(OutboundBlockedError):
        await guarded.structured_chat(
            [ChatMessage(role="user", content="sk-abcdefghijklmnopqrstuvwxyz012345")],
            _Answer,
        )

    record = usage_sink.records[-1]
    assert record.success is False
    assert record.blocked is True
    assert record.failure_reason == OutboundCategory.SECRET.value
    assert "sk-" not in record.model_dump_json()


# --------------------------------------------------------------------------- enforcement


_PROVIDER_CALL = re.compile(r"\.(?:structured_chat|stream_chat)\s*\(")


def test_no_module_calls_a_provider_without_wrapping_it_first() -> None:
    """B4 is worthless if a caller can hold a raw provider and talk to it.

    Inside ``factor_platform.llm`` the raw call is the implementation. Everywhere
    else, a module that reaches a provider must have wrapped it in a
    ``GuardedProvider`` first — otherwise a future caller quietly bypasses the
    boundary and nothing fails.
    """
    package = Path(factor_platform.__file__).parent
    llm_package = package / "llm"

    offenders = [
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if llm_package not in path.parents
        and _PROVIDER_CALL.search(text := path.read_text(encoding="utf-8"))
        and "GuardedProvider(" not in text
    ]
    assert offenders == []
