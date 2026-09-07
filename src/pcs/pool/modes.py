"""Mode-specific completed-session boundary helpers."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def _calendar(calendar):
    if calendar is None:
        import exchange_calendars as xc
        return xc.get_calendar("XNYS")
    if isinstance(calendar, str):
        import exchange_calendars as xc
        return xc.get_calendar(calendar)
    return calendar


@dataclass(frozen=True)
class OptionQuoteWindow:
    requested_mode: str
    market_state: str
    session: str
    decision_time_utc: str
    market_open_utc: str
    market_close_utc: str
    request_start_utc: str
    request_end_utc: str


def resolve_option_quote_window(decision_time, run_mode, exchange_calendar="XNYS") -> OptionQuoteWindow:
    """Resolve a timestamped options quote window from the exchange calendar."""
    mode = str(run_mode).upper()
    if mode not in {"PREMARKET", "INTRADAY", "EOD"}:
        raise ValueError("unsupported pool mode")
    decision = pd.Timestamp(decision_time)
    if pd.isna(decision) or decision.tzinfo is None:
        raise ValueError("OPTIONS_DECISION_TIMEZONE_REQUIRED")
    decision = decision.tz_convert("UTC")
    cal = _calendar(exchange_calendar)
    local_day = decision.tz_convert(str(cal.tz)).date()
    is_session = bool(cal.is_session(local_day))
    current_session = (pd.Timestamp(cal.date_to_session(local_day, direction="previous"))
                       if not is_session else pd.Timestamp(local_day))
    current_open = pd.Timestamp(cal.session_open(current_session)).tz_convert("UTC")
    current_close = pd.Timestamp(cal.session_close(current_session)).tz_convert("UTC")
    market_open = is_session and current_open <= decision <= current_close

    if mode == "INTRADAY" and market_open:
        session, start, end = current_session, current_open, decision
        state = "OPEN_INTRADAY"
    else:
        session = current_session
        if current_close > decision:
            session = pd.Timestamp(cal.previous_session(current_session))
        start = pd.Timestamp(cal.session_open(session)).tz_convert("UTC")
        end = pd.Timestamp(cal.session_close(session)).tz_convert("UTC")
        state = "MARKET_CLOSED_INTRADAY_REQUEST" if mode == "INTRADAY" else "CLOSED_RECENT_EOD"

    iso = lambda value: pd.Timestamp(value).tz_convert("UTC").isoformat()
    return OptionQuoteWindow(
        requested_mode=mode, market_state=state, session=str(session.date()),
        decision_time_utc=iso(decision), market_open_utc=iso(start),
        market_close_utc=iso(end), request_start_utc=iso(start),
        request_end_utc=iso(end),
    )


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
