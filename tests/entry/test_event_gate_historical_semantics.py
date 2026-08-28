import pandas as pd

from pcs.entry.gates import EventGate, GateStatus


class Candidate:
    ticker = "NVDA"
    entry_date = "2025-01-10"
    expiration = "2025-02-14"
    event_risk = 0
    trading_sessions = pd.date_range("2024-12-01", "2025-03-31", freq="B")


def calendar(*dates):
    return pd.DataFrame({"symbol": ["NVDA"] * len(dates), "event_type": ["EARNINGS"] * len(dates), "event_date": list(dates), "event_date_known_at_entry": ["YES"] * len(dates)})


def test_past_event_does_not_blackout():
    assert EventGate().evaluate(Candidate(), calendar("2025-01-03")).status == GateStatus.PASS


def test_future_event_within_three_business_days_blackouts():
    candidate = type("ShortExpiry", (Candidate,), {"expiration": "2025-01-13"})()
    result = EventGate().evaluate(candidate, calendar("2025-01-14"))
    assert result.status == GateStatus.PASS


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


def test_missing_calendar_is_fail_closed():
    result = EventGate().evaluate(Candidate(), None)
    assert result.status == GateStatus.FAIL
    assert "EVENT_CALENDAR_UNAVAILABLE" in result.reason_codes


def test_unverified_event_metadata_is_fail_closed():
    result = EventGate().evaluate(Candidate(), pd.DataFrame({
        "symbol": ["NVDA"], "event_type": ["EARNINGS"],
        "event_date": ["2025-01-20"], "event_date_known_at_entry": ["N/A"],
    }))
    assert result.status == GateStatus.FAIL
    assert "EVENT_CALENDAR_PIT_METADATA_UNVERIFIED" in result.reason_codes


def test_missing_calendar_file_is_optional(tmp_path):
    from pcs.research.variant_b_replay import _load_replay_calendar
    calendar = _load_replay_calendar(tmp_path / "missing.csv")
    assert calendar.empty
    assert calendar.attrs["event_status"] == "NOT_AVAILABLE"
