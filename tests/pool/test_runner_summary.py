import pandas as pd
from pcs.pool.runner import run_pcs_pool


class Access:
    def __init__(self, frame): self.frame = frame
    def read_prices(self, symbol, end_date=None): return self.frame.copy()


def test_runner_exposes_reconciliation_counts():
    d = pd.date_range("2025-01-01", periods=3)
    f = pd.DataFrame({"date": d, "open": [1, 2, 3], "high": [2, 3, 4],
                      "low": [1, 2, 3], "close": [1, 2, 3], "volume": [1, 1, 1]})
    result = run_pcs_pool(symbols=["AAA"], mode="EOD", as_of="2025-01-03", data_access=Access(f))
    for key in ("raw_count", "hard_excluded_count", "data_blocked_count", "dormant_count",
                "timing_watch_count", "timing_entry_ready_count", "options_check_count",
                "pcs_trade_ready_count", "temp_blocked_count", "rejected_count"):
        assert key in result.summary
