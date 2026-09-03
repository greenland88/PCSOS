import pandas as pd
from pcs.pool.modes import completed_daily_cutoff, resolve_effective_market_session


def test_premarket_excludes_current_session():
    frame = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=3)})
    assert completed_daily_cutoff(frame, "2025-01-03", "PREMARKET") == pd.Timestamp("2025-01-02")
    assert completed_daily_cutoff(frame, "2025-01-03", "EOD") == pd.Timestamp("2025-01-03")


def test_effective_session_uses_exchange_holiday_calendar():
    # 2026-09-07 is Labor Day; Tuesday's prior completed session is Friday.
    assert resolve_effective_market_session("2026-09-08", "PREMARKET", "XNYS") == pd.Timestamp("2026-09-04")


def test_effective_session_premarket_and_intraday_previous_session():
    assert resolve_effective_market_session("2026-09-03", "PREMARKET", "XNYS") == pd.Timestamp("2026-09-02")
    assert resolve_effective_market_session("2026-09-03", "INTRADAY", "XNYS") == pd.Timestamp("2026-09-02")


def test_effective_session_eod_requires_close():
    assert resolve_effective_market_session("2026-09-03", "EOD", "XNYS", "2026-09-03T19:00:00Z") == pd.Timestamp("2026-09-02")
    assert resolve_effective_market_session("2026-09-03", "EOD", "XNYS", "2026-09-03T21:00:00Z") == pd.Timestamp("2026-09-03")
