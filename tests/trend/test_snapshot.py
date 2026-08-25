import pandas as pd
import pytest

from pcs.trend import (
    TrendIndicatorConfig,
    TrendIndicatorValidationError,
    build_trend_snapshot,
)


def make_ohlcv(rows=260, start=100.0):
    dates = pd.date_range("2025-01-01", periods=rows, freq="D")
    close = pd.Series([start + i * 0.2 for i in range(rows)], index=dates)
    high = close + 0.5
    low = close - 0.5
    for index in (50, 100, 150, 220):
        if index < rows:
            high.iloc[index] += 8
            low.iloc[index] -= 8
    return pd.DataFrame({"date": dates, "open": close - 0.2, "high": high, "low": low, "close": close, "volume": 1_000_000}, index=dates)


def test_all_modules_available_with_benchmark():
    result = build_trend_snapshot(make_ohlcv(), make_ohlcv(start=90), symbol="TEST", benchmark="BM")
    assert result.available is True
    assert not result.warnings
    assert result.symbol == "TEST"
    assert result.benchmark == "BM"


def test_missing_benchmark_marks_only_relative_strength_unavailable():
    result = build_trend_snapshot(make_ohlcv())
    assert result.relative_strength.available is False
    assert "relative_strength_unavailable" in result.warnings
    assert result.ma_structure.available is True


def test_benchmark_insufficient_does_not_crash_snapshot():
    result = build_trend_snapshot(make_ohlcv(), make_ohlcv(rows=30))
    assert result.relative_strength.available is False
    assert "relative_strength_unavailable" in result.warnings


def test_as_of_date_is_shared_by_snapshot():
    stock = make_ohlcv()
    benchmark = make_ohlcv(start=90)
    result = build_trend_snapshot(stock, benchmark, as_of_date=stock["date"].iloc[-10])
    assert result.as_of_date == stock["date"].iloc[-10]
    assert result.pullback.available is True
    assert result.relative_strength.available is True


def test_inputs_are_not_modified():
    stock, benchmark = make_ohlcv(), make_ohlcv(start=90)
    stock_original, benchmark_original = stock.copy(deep=True), benchmark.copy(deep=True)
    build_trend_snapshot(stock, benchmark)
    pd.testing.assert_frame_equal(stock, stock_original)
    pd.testing.assert_frame_equal(benchmark, benchmark_original)


def test_snapshot_child_matches_direct_ma_call():
    from pcs.trend import analyze_ma_structure, calculate_base_indicators

    stock = make_ohlcv()
    config = TrendIndicatorConfig()
    indicators = calculate_base_indicators(stock, config)
    direct_input = pd.concat([stock[["close"]], indicators], axis=1)
    direct = analyze_ma_structure(direct_input, config)
    snapshot = build_trend_snapshot(stock, make_ohlcv(start=90), config)
    assert snapshot.ma_structure == direct


def test_snapshot_has_no_trade_decisions_or_score():
    result = build_trend_snapshot(make_ohlcv(), make_ohlcv(start=90))
    assert not hasattr(result, "trend_score")
    assert not any(hasattr(result, name) for name in ("entry", "roll", "action"))


def test_invalid_input_raises_validation_error():
    with pytest.raises(TrendIndicatorValidationError):
        build_trend_snapshot(make_ohlcv().drop(columns=["close"]))
