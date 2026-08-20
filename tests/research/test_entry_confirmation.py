import pandas as pd

from pcs.research.entry_confirmation import analyze_entry_confirmation


def bars(n=25):
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = [100.0] * n
    frame = pd.DataFrame({"date": dates, "open": close, "high": [101.0] * n,
                          "low": [99.0] * n, "close": close, "volume": [100.0] * n})
    return frame


def test_confirmation_signals_and_zero_body_are_safe():
    df = bars()
    df.loc[23, ["open", "close", "high", "low"]] = [101, 99, 102, 98]
    df.loc[24, ["open", "close", "high", "low", "volume"]] = [98.5, 101.5, 102, 98, 200]
    result = analyze_entry_confirmation(df, df.date.iloc[-1])
    assert result.bullish_engulfing
    assert result.confirmation_score >= 2


def test_aoi_boundaries_and_no_lookahead():
    df = bars()
    result = analyze_entry_confirmation(df, df.date.iloc[-1])
    future = df.copy()
    future.loc[len(future)] = [pd.Timestamp("2025-03-01"), 50, 51, 49, 50, 100]
    assert result.to_dict() == analyze_entry_confirmation(future, df.date.iloc[-1]).to_dict()


def test_missing_columns_rejected():
    with __import__("pytest").raises(ValueError):
        analyze_entry_confirmation(bars().drop(columns="volume"))
