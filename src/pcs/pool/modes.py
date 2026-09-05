"""Mode-specific completed-session boundary helpers."""
from __future__ import annotations

import pandas as pd


def _calendar(calendar):
    if calendar is None:
        import exchange_calendars as xc
        return xc.get_calendar("XNYS")
    if isinstance(calendar, str):
        import exchange_calendars as xc
        return xc.get_calendar(calendar)
    return calendar


def resolve_effective_market_session(requested_as_of, run_mode, exchange_calendar,
                                     market_timestamp=None):
    """Resolve the last completed *daily* exchange session for a pool run."""
    mode = str(run_mode).upper()
    if mode not in {"PREMARKET", "INTRADAY", "EOD", "HISTORICAL"}:
        raise ValueError("unsupported pool mode")
    cal = _calendar(exchange_calendar)
    requested = pd.Timestamp(requested_as_of)
    # exchange_calendars expects session labels as timezone-naive dates.
    session = pd.Timestamp(requested.date())
    # HISTORICAL is an explicit signal date and remains strictly PIT.
    if mode == "HISTORICAL":
        return session
    if not bool(cal.is_session(session.date())):
        session = pd.Timestamp(cal.date_to_session(session, direction="previous"))
    if mode in {"PREMARKET", "INTRADAY"}:
        return pd.Timestamp(cal.previous_session(session)).normalize()
    # EOD includes today's bar only after the exchange close.  A supplied
    # timestamp makes this deterministic in tests and replay artifacts.
    now = pd.Timestamp(market_timestamp) if market_timestamp is not None else requested
    close = pd.Timestamp(cal.session_close(session))
    if close.tzinfo is not None and now.tzinfo is None:
        now = now.tz_localize("UTC")
    if now >= close:
        return session
    return pd.Timestamp(cal.previous_session(session)).normalize()


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
