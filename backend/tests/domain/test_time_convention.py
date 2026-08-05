"""Time convention contract.

Not reading a future *field* does not prevent a future *function*: a factor
computed from the T close is not knowable before the T close, so it cannot be
traded at T. These invariants are enforced in the contract rather than left to
downstream validation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from factor_platform.domain.time_convention import TimeConvention, offset_of


def test_default_convention_computes_at_t_close_and_trades_next_day() -> None:
    convention = TimeConvention()
    assert convention.observation_time == "T_CLOSE"
    assert convention.information_available_time == "T_AFTER_CLOSE"
    assert convention.signal_date == "T"
    assert convention.trade_date == "T+1"
    assert convention.execution_price == "NEXT_OPEN"


def test_default_announcement_policy_is_conservative() -> None:
    assert TimeConvention().announcement_timing_policy == "conservative"


def test_trading_on_the_signal_day_after_close_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not available"):
        TimeConvention(
            information_available_time="T_AFTER_CLOSE",
            signal_date="T",
            trade_date="T",
        )


def test_intraday_information_may_be_traded_same_day() -> None:
    convention = TimeConvention(
        observation_time="T_OPEN",
        information_available_time="T_INTRADAY",
        signal_date="T",
        trade_date="T",
        execution_price="NEXT_CLOSE",
    )
    assert convention.trade_date == "T"


def test_forward_return_cannot_start_before_the_trade_date() -> None:
    with pytest.raises(ValidationError, match="forward_return_start"):
        TimeConvention(trade_date="T+1", forward_return_start="T_OPEN")


def test_offset_parsing_handles_plain_and_shifted_labels() -> None:
    assert offset_of("T") == 0
    assert offset_of("T+1") == 1
    assert offset_of("T+5") == 5
    assert offset_of("T+1_OPEN") == 1
    assert offset_of("T_CLOSE") == 0


def test_malformed_day_label_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TimeConvention(trade_date="tomorrow")


def test_convention_is_versioned_for_invalidation() -> None:
    assert TimeConvention().version == 1
