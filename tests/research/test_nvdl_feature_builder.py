import pandas as pd
from pcs.research.nvdl_feature_builder import build_nvdl_features


def test_nvdl_feature_builder_produces_pit_columns(monkeypatch):
    dates = pd.date_range("2025-01-01", periods=60, freq="D")
    daily = pd.DataFrame({"date": dates, "open": range(100, 160), "high": range(101, 161),
                          "low": range(99, 159), "close": range(100, 160), "volume": 100})
    market = pd.DataFrame({"date": dates, "breadth_positive": True})
    class Access:
        def read_prices(self, *args): return daily
    monkeypatch.setattr(pd, "read_parquet", lambda path: market)
    out, mkt = build_nvdl_features(Access())
    assert {"atr", "extension20_atr", "return_5d", "breakout_state"}.issubset(out.columns)
    assert len(mkt) == 60
