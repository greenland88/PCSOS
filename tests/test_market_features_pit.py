from pcs.features.market_features import calculate_market_features
import pandas as pd


def _rows(start, count):
    return [{"symbol": "X", "date": (pd.Timestamp("2020-01-01") + pd.Timedelta(days=i-1)).date(), "open": i,
             "high": i + 1, "low": i - 1, "close": i, "volume": 100}
            for i in range(start, start + count)]


def test_future_rows_do_not_change_historical_market_features():
    base = calculate_market_features(_rows(1, 30))
    extended = calculate_market_features(_rows(1, 30) + _rows(31, 10))
    fields = ("dma20", "dma50", "dma200", "atr5", "atr14",
              "realized_vol_20d", "predictability_score", "trend_score")
    for before, after in zip(base, extended[:len(base)]):
        for field in fields:
            if pd.isna(before[field]):
                assert pd.isna(after[field])
            else:
                assert before[field] == after[field]


def test_realized_vol_requires_full_lookback():
    rows = calculate_market_features(_rows(1, 25))
    assert all(pd.isna(row["realized_vol_20d"]) for row in rows[:20])
    assert pd.notna(rows[20]["realized_vol_20d"])


def test_scores_are_missing_until_full_feature_readiness():
    rows = calculate_market_features(_rows(1, 205))
    assert all(not row["feature_ready"] for row in rows[:199])
    assert all(pd.isna(row["trend_score"]) for row in rows[:199])
    assert rows[199]["feature_ready"]
