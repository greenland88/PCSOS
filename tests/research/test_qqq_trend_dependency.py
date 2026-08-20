import pandas as pd

from pcs.trend.snapshot import build_trend_snapshot


def frame(n=220):
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = pd.Series(range(100, 100+n), dtype=float)
    return pd.DataFrame({"date": dates, "open": close, "high": close+1, "low": close-1, "close": close, "volume": 1000.})


def test_undefined_benchmark_makes_only_relative_strength_unavailable():
    result = build_trend_snapshot(frame())
    assert result.relative_strength.available is False
    assert "relative_strength_unavailable" in result.warnings


def test_snapshot_does_not_mutate_source():
    source = frame(); original = source.copy(deep=True)
    build_trend_snapshot(source)
    pd.testing.assert_frame_equal(source, original)
