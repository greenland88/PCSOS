from dataclasses import replace

import pandas as pd
import pytest

from pcs.trend import TrendIndicatorConfig, TrendIndicatorValidationError, analyze_relative_strength


def make_ohlcv(closes, dates=None):
    dates = pd.date_range("2025-01-01", periods=len(closes), freq="D") if dates is None else pd.DatetimeIndex(dates)
    close = pd.Series(closes, index=dates, dtype=float)
    return pd.DataFrame({"date": dates, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1_000_000}, index=dates)


def config():
    return TrendIndicatorConfig()


def test_stock_outperforms_benchmark():
    stock = make_ohlcv(range(100, 201))
    benchmark = make_ohlcv([100.0] * 101)
    result = analyze_relative_strength(stock, benchmark, config())
    assert result.available and result.relative_return_5d > 0
    assert result.rs_state in {"strong", "improving"}


def test_stock_underperforms_benchmark_and_is_weak():
    stock = make_ohlcv(range(200, 99, -1))
    benchmark = make_ohlcv([100 + i * 0.5 for i in range(101)])
    result = analyze_relative_strength(stock, benchmark, config())
    assert result.relative_return_5d < 0 and result.relative_return_60d < 0
    assert result.rs_state == "weak"


def test_short_term_weakness_with_long_term_strength_is_weakening():
    benchmark = make_ohlcv([100 + i for i in range(81)] + [180.0] * 20)
    stock = make_ohlcv([100 + i * 5 for i in range(81)] + [500 - i * 2 for i in range(20)])
    result = analyze_relative_strength(stock, benchmark, config())
    assert result.relative_return_60d > 0
    assert result.relative_return_5d < 0 and result.relative_return_20d < 0
    assert result.rs_state == "weakening"


def test_benchmark_and_stock_both_fall_not_stock_specific():
    stock = make_ohlcv(range(300, 199, -1))
    benchmark = make_ohlcv(range(200, 99, -1))
    result = analyze_relative_strength(stock, benchmark, config())
    assert result.stock_specific_weakness is False


def test_stable_benchmark_and_falling_stock_is_stock_specific():
    benchmark = make_ohlcv([100.0] * 101)
    stock = make_ohlcv(range(200, 99, -1))
    result = analyze_relative_strength(stock, benchmark, config())
    assert result.stock_specific_weakness is True


def test_inner_align_uses_only_common_dates():
    dates = pd.date_range("2025-01-01", periods=101, freq="D")
    stock = make_ohlcv(range(100, 201), dates)
    benchmark_dates = dates[:-1].append(pd.DatetimeIndex([dates[-1] + pd.Timedelta(days=1)]))
    benchmark = make_ohlcv(range(100, 201), benchmark_dates)
    result = analyze_relative_strength(stock, benchmark, config())
    assert result.available is True
    assert result.stock_return_5d == pytest.approx(5 / 194)


def test_as_of_date_excludes_future_rows():
    stock = make_ohlcv(range(100, 201))
    benchmark = make_ohlcv(range(100, 201))
    result = analyze_relative_strength(stock, benchmark, config(), as_of_date=stock.index[80])
    expected = 180 / 175
    assert result.stock_return_5d == pytest.approx(expected - 1)


def test_insufficient_data_is_unavailable():
    result = analyze_relative_strength(make_ohlcv(range(20)), make_ohlcv(range(20)), config())
    assert result.available is False
    assert result.rs_state is None


def test_missing_columns_raise_validation_error():
    with pytest.raises(TrendIndicatorValidationError):
        analyze_relative_strength(make_ohlcv(range(70)).drop(columns=["volume"]), make_ohlcv(range(70)), config())


def test_input_dataframes_are_not_modified():
    stock = make_ohlcv(range(70))
    benchmark = make_ohlcv(range(70))
    stock_original = stock.copy(deep=True)
    benchmark_original = benchmark.copy(deep=True)
    analyze_relative_strength(stock, benchmark, config())
    pd.testing.assert_frame_equal(stock, stock_original)
    pd.testing.assert_frame_equal(benchmark, benchmark_original)


def test_threshold_changes_can_change_state():
    stock = make_ohlcv(range(100, 201))
    benchmark = make_ohlcv([100.0] * 101)
    default = analyze_relative_strength(stock, benchmark, config())
    strict_config = replace(config(), rs_strong_threshold=1.0, rs_improving_threshold=0.5)
    strict = analyze_relative_strength(stock, benchmark, strict_config)
    assert default.rs_state != strict.rs_state
