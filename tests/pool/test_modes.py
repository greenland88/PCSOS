import pandas as pd
from pcs.pool.modes import completed_daily_cutoff


def test_premarket_excludes_current_session():
    frame = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=3)})
    assert completed_daily_cutoff(frame, "2025-01-03", "PREMARKET") == pd.Timestamp("2025-01-02")
    assert completed_daily_cutoff(frame, "2025-01-03", "EOD") == pd.Timestamp("2025-01-03")
