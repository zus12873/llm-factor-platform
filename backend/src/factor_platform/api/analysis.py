"""Factor analysis endpoint.

Takes a computed factor and the forward returns to score it against, and returns
IC, quantile spreads, turnover and coverage.

Coverage and the skipped-date count travel with the numbers rather than being
available on request. A researcher reading an IC of 0.08 needs to know whether it
came from 250 dates or from 12 — and if that has to be looked up separately, it
will not be.
"""

from __future__ import annotations

from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Body
from pydantic import BaseModel, Field

from factor_platform.analysis.metrics import AnalysisResult, analyze_factor

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


class FramePayload(BaseModel):
    """A date-indexed matrix in a JSON-friendly shape."""

    dates: list[str]
    codes: list[str]
    values: list[list[float | None]]

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            self.values, index=pd.to_datetime(self.dates), columns=self.codes
        )


class AnalysisRequest(BaseModel):
    factor: FramePayload
    forward_returns: FramePayload
    groups: int = Field(default=5, ge=2, le=20)


@router.post("")
async def analyze(
    body: Annotated[AnalysisRequest, Body()],
) -> AnalysisResult:
    """Evaluate a factor against forward returns."""
    return analyze_factor(
        body.factor.to_frame(),
        body.forward_returns.to_frame(),
        groups=body.groups,
    )


__all__ = ["AnalysisRequest", "FramePayload", "router"]
