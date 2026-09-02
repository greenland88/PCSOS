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
    assert result.counters["ordinary_reader_calls"] == 2
    assert result.counters["provider_calls"] == 0


def test_worker_count_does_not_change_decisions():
    dates = pd.date_range("2025-01-01", periods=80, freq="D")
    frame = pd.DataFrame({"date": dates, "open": range(80), "high": range(1, 81),
                          "low": range(80), "close": range(1, 81), "volume": [1000] * 80})
    access = FakeAccess({"QQQ": frame, "AAA": frame, "BBB": frame})
    one = run_pcs_pool(symbols=["AAA", "BBB"], as_of="2025-03-21", mode="EOD", max_workers=1, data_access=access)
    many = run_pcs_pool(symbols=["AAA", "BBB"], as_of="2025-03-21", mode="EOD", max_workers=4, data_access=access)
    assert [(r.symbol, r.final_action, r.reason_codes) for r in one.ticker_results] == \
           [(r.symbol, r.final_action, r.reason_codes) for r in many.ticker_results]


def test_one_ticker_failure_does_not_abort_batch():
    dates = pd.date_range("2025-01-01", periods=80, freq="D")
    frame = pd.DataFrame({"date": dates, "open": range(80), "high": range(1, 81),
                          "low": range(80), "close": range(1, 81), "volume": [1000] * 80})
    access = FakeAccess({"QQQ": frame, "GOOD": frame})
    result = run_pcs_pool(symbols=["GOOD", "BAD"], as_of="2025-03-21", mode="EOD", data_access=access)
    assert {row.symbol for row in result.ticker_results} == {"GOOD", "BAD"}
    good = next(row for row in result.ticker_results if row.symbol == "GOOD")
    bad = next(row for row in result.ticker_results if row.symbol == "BAD")
    assert good.symbol == "GOOD" and bad.symbol == "BAD"
    assert "DAILY_TIMING_FAILED" in bad.reason_codes
    assert good.reason_codes != bad.reason_codes
