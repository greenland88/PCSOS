from pcs.research.run_pcs_backtest import parse_args
from pcs.research.run_pcs_backtest import _load_pit_window
import pandas as pd


def test_cli_args():
    args = parse_args(["--symbol", "QQQ", "--benchmark", "RSP", "--start-date", "2026-06-01", "--end-date", "2026-06-30", "--run-label", "smoke"])
    assert (args.symbol, args.benchmark, args.run_label) == ("QQQ", "RSP", "smoke")


def test_pit_window_includes_warmup_but_keeps_end_boundary():
    class Access:
        def read_prices(self, symbol, start, end):
            assert symbol == "QQQ"
            assert pd.Timestamp(start) == pd.Timestamp("2024-02-16")
            assert pd.Timestamp(end) == pd.Timestamp("2025-01-01")
            return pd.DataFrame({"date": ["2024-02-15", "2025-01-01"], "close": [1, 2]})

    out = _load_pit_window(Access(), "QQQ", pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-01"))
    assert out.date.tolist() == [pd.Timestamp("2024-02-15"), pd.Timestamp("2025-01-01")]
