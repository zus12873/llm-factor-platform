"""Time convention: when a factor is knowable, and when it may be acted on.

Point-in-time correctness has two halves. The first — never read a field whose
value was not yet published — is handled by the query layer. The second is here:
a factor computed from the T close is *not knowable* before the T close, so it
cannot be traded at T no matter how clean the underlying data is. Leaving that
implicit is how a pipeline that reads no future field still produces a future
function.

Day labels are written ``T``, ``T+1``, ``T+5``, optionally suffixed with the
intraday moment (``T+1_OPEN``). ``T+N`` is a symbolic horizon used by forward
return windows whose length is supplied at analysis time.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, field_validator, model_validator

_DAY_LABEL: Final = re.compile(r"^T(?:\+(?:\d+|N))?(?:_(?:OPEN|CLOSE))?$")
_OFFSET: Final = re.compile(r"^T(?:\+(\d+|N))?")

SYMBOLIC_HORIZON: Final = "N"


class ObservationTime(StrEnum):
    """When the underlying datum is stamped."""

    T_OPEN = "T_OPEN"
    T_CLOSE = "T_CLOSE"
    REPORT_PERIOD = "REPORT_PERIOD"


class InformationAvailableTime(StrEnum):
    """When a researcher could actually have known the value."""

    T_INTRADAY = "T_INTRADAY"
    T_AFTER_CLOSE = "T_AFTER_CLOSE"
    ANNOUNCEMENT_BEFORE_OPEN = "ANNOUNCEMENT_BEFORE_OPEN"
    ANNOUNCEMENT_AFTER_CLOSE = "ANNOUNCEMENT_AFTER_CLOSE"


class ExecutionPrice(StrEnum):
    NEXT_OPEN = "NEXT_OPEN"
    NEXT_CLOSE = "NEXT_CLOSE"
    VWAP = "VWAP"
    NONE = "NONE"


class AnnouncementTimingPolicy(StrEnum):
    """What to assume when the publication moment is unknown.

    ``conservative`` defers to the next session, which is the only choice that
    cannot manufacture look-ahead.
    """

    CONSERVATIVE = "conservative"
    AS_REPORTED = "as_reported"


def offset_of(label: str) -> int:
    """Return the trading-day offset encoded in a day label.

    ``T`` and ``T_CLOSE`` are 0; ``T+1`` and ``T+1_OPEN`` are 1. Raises
    :class:`ValueError` for the symbolic ``T+N`` horizon, which has no fixed
    offset until an analysis window is chosen.
    """
    match = _OFFSET.match(label)
    if match is None:
        raise ValueError(f"malformed day label: {label}")
    captured = match.group(1)
    if captured is None:
        return 0
    if captured == SYMBOLIC_HORIZON:
        raise ValueError(f"symbolic horizon has no fixed offset: {label}")
    return int(captured)


def _is_symbolic(label: str) -> bool:
    return f"+{SYMBOLIC_HORIZON}" in label


# Information that only exists once the session is over cannot be acted on
# during that session.
_AFTER_SESSION: Final = frozenset(
    {
        InformationAvailableTime.T_AFTER_CLOSE,
        InformationAvailableTime.ANNOUNCEMENT_AFTER_CLOSE,
    }
)


class TimeConvention(BaseModel):
    """The signal/trade timing contract carried by a spec, plan and artifact.

    Defaults describe the首期 house convention: observe at the T close, treat it
    as knowable only after that close, form the signal on T, trade at the T+1
    open, and measure forward returns from that same open.
    """

    schema_version: int = 1
    version: int = 1

    observation_time: ObservationTime = ObservationTime.T_CLOSE
    information_available_time: InformationAvailableTime = (
        InformationAvailableTime.T_AFTER_CLOSE
    )
    signal_date: str = "T"
    trade_date: str = "T+1"
    execution_price: ExecutionPrice = ExecutionPrice.NEXT_OPEN
    forward_return_start: str = "T+1_OPEN"
    forward_return_end: str = "T+N_OPEN"
    announcement_timing_policy: AnnouncementTimingPolicy = (
        AnnouncementTimingPolicy.CONSERVATIVE
    )

    @field_validator("signal_date", "trade_date", "forward_return_start", "forward_return_end")
    @classmethod
    def _validate_day_label(cls, value: str) -> str:
        if not _DAY_LABEL.match(value):
            raise ValueError(
                f"malformed day label: {value!r} (expected T, T+<n>, T+N, optionally _OPEN/_CLOSE)"
            )
        return value

    @model_validator(mode="after")
    def _enforce_availability(self) -> TimeConvention:
        signal = offset_of(self.signal_date)
        trade = offset_of(self.trade_date)

        earliest_trade = signal + 1 if self.information_available_time in _AFTER_SESSION else signal
        if trade < earliest_trade:
            raise ValueError(
                f"signal is not available for trading at {self.trade_date}: "
                f"{self.information_available_time.value} implies the earliest "
                f"trade date is T+{earliest_trade}"
            )

        if (
            not _is_symbolic(self.forward_return_start)
            and offset_of(self.forward_return_start) < trade
        ):
            raise ValueError(
                f"forward_return_start ({self.forward_return_start}) precedes "
                f"trade_date ({self.trade_date})"
            )
        return self


__all__ = [
    "AnnouncementTimingPolicy",
    "ExecutionPrice",
    "InformationAvailableTime",
    "ObservationTime",
    "TimeConvention",
    "offset_of",
]
