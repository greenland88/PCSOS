from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from pcs.trend import TrendIndicatorConfig, TrendIndicatorValidationError, analyze_trend_cleanliness


def make_data(rows=80, close=None, sma20=None, sma50=None, atr=None, gap=None):
    index = pd.date_range("2025-01-01", periods=rows, freq="D")
    close = pd.Series(np.linspace(100, 140, rows) if close is None else close, index=index, dtype=float)
    sma20 = pd.Series(close - 2 if sma20 is None else sma20, index=index, dtype=float)
    sma50 = pd.Series(close - 5 if sma50 is None else sma50, index=index, dtype=float)
    atr = pd.Series(np.ones(rows) if atr is None else atr, index=index, dtype=float)
    open_values = close.shift(1).fillna(close).to_numpy() if gap is None else np.asarray(gap, dtype=float)
    ohlcv = pd.DataFrame({"date": index, "open": open_values, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1_000_000}, index=index)
    indicators = pd.DataFrame({"sma20": sma20, "sma50": sma50, "atr14": atr}, index=index)
    return ohlcv, indicators


def test_smooth_uptrend_is_clean():
    ohlcv, indicators = make_data()
    result = analyze_trend_cleanliness(ohlcv, indicators)
    assert result.available and result.cleanliness_state == "clean"


def test_pullback_is_acceptable():
    close = np.linspace(100, 140, 80)
    close[50:55] -= [10, 15, 10, 5, 0]
    ohlcv, indicators = make_data(close=close)
    result = analyze_trend_cleanliness(ohlcv, indicators)
    assert result.cleanliness_state in {"acceptable", "noisy"}


def test_frequent_ma20_crossings_are_noisy():
    close = np.array([100 + (i % 2) * 4 for i in range(80)], dtype=float)
    ohlcv, indicators = make_data(close=close, sma20=np.full(80, 102), sma50=np.full(80, 100), atr=np.full(80, 1.0))
    result = analyze_trend_cleanliness(ohlcv, indicators)
    assert result.ma20_crossings >= 4
    assert result.cleanliness_state in {"noisy", "chaotic"}


def test_crossings_and_large_moves_are_chaotic():
    close = np.array([100 + (i % 2) * 10 for i in range(80)], dtype=float)
    ohlcv, indicators = make_data(close=close, sma20=np.full(80, 105), sma50=np.full(80, 105), atr=np.full(80, 1.0))
    result = analyze_trend_cleanliness(ohlcv, indicators)
    assert result.cleanliness_state == "chaotic"


def test_gaps_are_counted():
    ohlcv, indicators = make_data()
    ohlcv.iloc[60:, ohlcv.columns.get_loc("open")] = ohlcv["close"].shift(1).iloc[60:] * 1.05
    result = analyze_trend_cleanliness(ohlcv, indicators)
    assert result.gap_count > 0


def test_large_and_extreme_moves_are_counted():
    close = np.linspace(100, 140, 80)
    close[40:] = close[39] + np.arange(40) * 3
    ohlcv, indicators = make_data(close=close, atr=np.ones(80))
    result = analyze_trend_cleanliness(ohlcv, indicators)
    assert result.large_move_count > 0
    assert result.extreme_move_count > 0


def test_slope_direction_changes_are_counted():
    sma20 = np.array([100 + ((-1) ** i) * 2 for i in range(80)], dtype=float)
    sma50 = np.array([100 + ((-1) ** i) * 1 for i in range(80)], dtype=float)
    ohlcv, indicators = make_data(sma20=sma20, sma50=sma50)
    result = analyze_trend_cleanliness(ohlcv, indicators)
    assert result.slope_direction_change_count > 10


def test_insufficient_data_is_unavailable():
    ohlcv, indicators = make_data(rows=30)
    assert analyze_trend_cleanliness(ohlcv, indicators).available is False


def test_missing_columns_raise_validation_error():
    ohlcv, indicators = make_data()
    with pytest.raises(TrendIndicatorValidationError):
        analyze_trend_cleanliness(ohlcv.drop(columns=["volume"]), indicators)


def test_inputs_are_not_modified():
    ohlcv, indicators = make_data()
    original_ohlcv, original_indicators = ohlcv.copy(deep=True), indicators.copy(deep=True)
    analyze_trend_cleanliness(ohlcv, indicators)
    pd.testing.assert_frame_equal(ohlcv, original_ohlcv)
    pd.testing.assert_frame_equal(indicators, original_indicators)


def test_as_of_date_excludes_future_rows():
    ohlcv, indicators = make_data()
    result = analyze_trend_cleanliness(ohlcv, indicators, as_of_date=ohlcv.index[-1])
    future = analyze_trend_cleanliness(ohlcv, indicators, as_of_date=ohlcv.index[-20])
    assert result.available is True
    assert future.available is True
    assert future.current_atr_pct != result.current_atr_pct


def test_config_can_change_state():
    close = np.array([100 + (i % 2) * 4 for i in range(80)], dtype=float)
    ohlcv, indicators = make_data(close=close, sma20=np.full(80, 102), sma50=np.full(80, 100), atr=np.full(80, 1.0))
    default = analyze_trend_cleanliness(ohlcv, indicators)
    relaxed = replace(
        TrendIndicatorConfig(),
        cleanliness_noisy_min_crossings=100,
        cleanliness_chaotic_min_crossings=200,
        cleanliness_large_move_severe_threshold=2.0,
        cleanliness_extreme_move_elevated_threshold=2.0,
        cleanliness_chaotic_min_extreme_move_ratio=2.0,
    )
    changed = analyze_trend_cleanliness(ohlcv, indicators, relaxed)
    assert default.cleanliness_state != changed.cleanliness_state
