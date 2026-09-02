import pandas as pd
from types import SimpleNamespace
from pcs.pool.runner import run_pcs_pool


class Access:
    def __init__(self, frame): self.frame = frame
    def read_prices(self, symbol, end_date=None): return self.frame.copy()
    def read_verified_dataset(self, handle, end_date=None, required_warmup_rows=0): return self.frame.copy()


def resolve_handle(symbol, as_of, warmup, *, data_access):
    return SimpleNamespace(ticker=symbol, verification_status="VERIFIED")


def test_runner_resolves_explicit_universe_file(tmp_path):
    path = tmp_path / "universe.csv"
    path.write_text("symbol\nAAA\n", encoding="utf-8")
    dates = pd.date_range("2025-01-01", periods=3)
    frame = pd.DataFrame({"date": dates, "open": [1, 2, 3], "high": [2, 3, 4],
                          "low": [1, 2, 3], "close": [1, 2, 3], "volume": [1, 1, 1]})
    result = run_pcs_pool(universe_id=str(path), mode="EOD", as_of="2025-01-03",
                          data_access=Access(frame), daily_handle_resolver=resolve_handle)
    assert [row.symbol for row in result.ticker_results] == ["AAA"]
