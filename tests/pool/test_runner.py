import pandas as pd

from pcs.pool.runner import run_pcs_pool


class FakeAccess:
    def __init__(self, frames):
        self.frames = frames
        self.quote_calls = 0

    def read_prices(self, symbol, end_date=None):
        return self.frames[symbol].copy()


def test_runner_returns_one_result_per_symbol_and_does_not_read_options():
    dates = pd.date_range("2025-01-01", periods=80, freq="D")
    frame = pd.DataFrame({"date": dates, "open": range(80), "high": range(1, 81),
                          "low": range(80), "close": range(1, 81), "volume": [1000] * 80})
    access = FakeAccess({"QQQ": frame, "AAA": frame})
    result = run_pcs_pool(symbols=["AAA"], as_of="2025-03-21", mode="PREMARKET", data_access=access)
    assert len(result.ticker_results) == 1
    assert result.summary["missing_ticker_decisions"] == 0
    assert result.summary["options_check_count"] == 0
