import pandas as pd

from pcs.research.variant_b_replay import _event_reason


def _calendar(date):
    return pd.DataFrame({
        "symbol": ["NVDA"],
        "event_type": ["EARNINGS"],
        "event_date": [pd.Timestamp(date)],
    })


def test_variant_b_does_not_approximate_exchange_sessions_with_weekdays():
    result = _event_reason(
        _calendar("2025-01-21"), "NVDA", pd.Timestamp("2025-01-17"),
        pd.Timestamp("2025-01-20"), trading_sessions=None,
    )

    assert result == "EVENT_TRADING_CALENDAR_UNAVAILABLE"


def test_variant_b_uses_supplied_exchange_sessions():
    sessions = pd.DatetimeIndex(["2025-01-17", "2025-01-21"])
    result = _event_reason(
        _calendar("2025-01-21"), "NVDA", pd.Timestamp("2025-01-17"),
        pd.Timestamp("2025-01-20"), trading_sessions=sessions,
    )

    assert result == "EVENT_PRE_EARNINGS_BLACKOUT"
