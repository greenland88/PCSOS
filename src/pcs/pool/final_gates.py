"""Event-policy adapter for the pool funnel; fail closed on missing PIT data."""
from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from pcs.entry.gates import EventGate, GateStatus


@dataclass(frozen=True)
class PoolEventResult:
    status: str
    reason_codes: tuple[str, ...] = ()
    force_exit_date: str | None = None
    exit_buffer_sessions: int | None = None
    event_date: str | None = None


def evaluate_pool_event(candidate, calendar: pd.DataFrame | None, *, policy: str = "HOLD_TO_EXPIRY",
                        planned_exit_before_event_sessions: int | None = None,
                        trading_sessions=None) -> PoolEventResult:
    if policy not in {"HOLD_TO_EXPIRY", "PLANNED_EARLY_EXIT"}:
        raise ValueError("unsupported event policy")
    if policy == "HOLD_TO_EXPIRY":
        result = EventGate().evaluate(candidate, calendar)
        return PoolEventResult("EVENT_PASS" if result.status == GateStatus.PASS else "EVENT_BLOCKED",
                               tuple(result.reason_codes))
    if planned_exit_before_event_sessions is None or planned_exit_before_event_sessions < 1:
        return PoolEventResult("EVENT_BLOCKED", ("EXIT_BUFFER_SESSIONS_REQUIRED",))
    if calendar is None or calendar.empty or "event_date" not in calendar.columns or "event_date_known_at_entry" not in calendar.columns:
        return PoolEventResult("EVENT_DATA_STALE", ("EVENT_PIT_DATA_MISSING",))
    known = calendar[calendar["event_date_known_at_entry"].astype(str).str.upper().isin({"YES", "TRUE", "1"})]
    if known.empty:
        return PoolEventResult("EVENT_DATA_STALE", ("EVENT_PIT_METADATA_UNVERIFIED",))
    entry = pd.Timestamp(getattr(candidate, "entry_date", None)).normalize()
    expiry = pd.Timestamp(getattr(candidate, "expiration", None)).normalize()
    events = sorted(pd.to_datetime(known.event_date).dt.normalize())
    event = next((date for date in events if entry <= date <= expiry), None)
    if event is None:
        return PoolEventResult("EVENT_PASS")
    sessions = pd.DatetimeIndex(pd.to_datetime(trading_sessions if trading_sessions is not None else [] )).normalize()
    if entry not in sessions or event not in sessions:
        return PoolEventResult("EVENT_DATA_STALE", ("TRADING_SESSION_CALENDAR_UNAVAILABLE",), event_date=str(event.date()))
    event_pos = sessions.get_loc(event)
    exit_pos = event_pos - planned_exit_before_event_sessions
    if exit_pos <= sessions.get_loc(entry):
        return PoolEventResult("EVENT_BLOCKED", ("EVENT_EXIT_BUFFER_BEFORE_ENTRY",), event_date=str(event.date()))
    force_exit = sessions[exit_pos]
    if force_exit >= event:
        return PoolEventResult("EVENT_BLOCKED", ("FORCE_EXIT_NOT_BEFORE_EVENT",), event_date=str(event.date()))
    return PoolEventResult("EVENT_MANAGED_CONDITIONAL", (), str(force_exit.date()),
                           planned_exit_before_event_sessions, str(event.date()))
