"""Mode-specific completed-session boundary helpers."""
from __future__ import annotations

import pandas as pd


def completed_daily_cutoff(frame: pd.DataFrame, as_of, mode: str):
    if mode not in {"PREMARKET", "INTRADAY", "EOD"}:
        raise ValueError("unsupported pool mode")
    if frame is None or frame.empty or "date" not in frame.columns:
        return None
    dates = pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="coerce").dropna()).normalize().unique()
    dates = dates.sort_values()
    cutoff = pd.Timestamp(as_of).normalize()
    available = dates[dates <= cutoff]
    if len(available) == 0:
        return None
    # PREMARKET/INTRADAY are pre-close by contract; EOD includes as-of only
    # when that completed daily bar is actually present.
    if mode in {"PREMARKET", "INTRADAY"} and available[-1] == cutoff:
        available = available[:-1]
    return available[-1] if len(available) else None
