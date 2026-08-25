import pandas as pd

from pcs.entry.gates import EventGate, GateStatus


class Candidate:
    ticker = "NVDA"
    entry_date = "2025-01-10"
    expiration = "2025-02-14"
    event_risk = 0


def calendar(*dates):
    return pd.DataFrame({"symbol": ["NVDA"] * len(dates), "event_type": ["EARNINGS"] * len(dates), "event_date": list(dates)})


def test_past_event_does_not_blackout():
    assert EventGate().evaluate(Candidate(), calendar("2025-01-03")).status == GateStatus.PASS


def test_future_event_within_three_business_days_blackouts():
    candidate = type("ShortExpiry", (Candidate,), {"expiration": "2025-01-13"})()
    result = EventGate().evaluate(candidate, calendar("2025-01-14"))
    assert result.reason_codes == ("EVENT_PRE_EARNINGS_BLACKOUT",)


def test_future_event_before_expiration_rejects_crossing():
    result = EventGate().evaluate(Candidate(), calendar("2025-01-20"))
    assert result.reason_codes == ("EVENT_EARNINGS_CROSSING",)


def test_event_after_expiration_is_allowed():
    assert EventGate().evaluate(Candidate(), calendar("2025-03-01")).status == GateStatus.PASS


def test_past_and_future_events_ignore_past_event():
    result = EventGate().evaluate(Candidate(), calendar("2024-12-01", "2025-01-20"))
    assert result.reason_codes == ("EVENT_EARNINGS_CROSSING",)


def test_no_events_is_allowed():
    assert EventGate().evaluate(Candidate(), calendar()).status == GateStatus.PASS


def test_pit_calendar_requires_exchange_sessions():
    c = calendar("2025-01-20").assign(event_date_known_at_entry=True)
    c.attrs["historical_pit_required"] = True
    result = EventGate().evaluate(Candidate(), c)
    assert result.reason_codes == ("EVENT_TRADING_CALENDAR_UNAVAILABLE",)
