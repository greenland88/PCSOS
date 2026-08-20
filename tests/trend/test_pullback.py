from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from pcs.trend import TrendIndicatorConfig, TrendIndicatorValidationError, analyze_pullback


def make_data(close=100.0, recent_high=100.0, rows=60, sma20=98.0, sma50=95.0, atr=2.0):
    dates = pd.date_range("2025-01-01", periods=rows, freq="D")
    close_values = np.full(rows, close, dtype=float)
    close_values[-1] = close
    high_values = np.full(rows, close, dtype=float)
    high_values[-1] = recent_high
    ohlcv = pd.DataFrame({"date": dates, "open": close_values, "high": high_values, "low": close_values - 1, "close": close_values, "volume": 1_000_000})
    indicators = pd.DataFrame({"sma20": np.full(rows, sma20), "sma50": np.full(rows, sma50), "atr14": np.full(rows, atr)})
    return ohlcv, indicators


def structures(state="bullish"):
    return SimpleNamespace(structure_state=state), SimpleNamespace(structure_state=state)


def test_no_pullback():
    ohlcv, indicators = make_data(close=100, recent_high=100)
    result = analyze_pullback(ohlcv, indicators, *structures())
    assert result.pullback_state == "no_pullback"


def test_shallow_pullback():
    ohlcv, indicators = make_data(close=97, recent_high=100, sma20=95, sma50=90)
    result = analyze_pullback(ohlcv, indicators, *structures())
    assert result.pullback_state == "shallow_pullback"


def test_healthy_pullback_near_sma20():
    ohlcv, indicators = make_data(close=92, recent_high=100, sma20=93, sma50=88)
    result = analyze_pullback(ohlcv, indicators, *structures())
    assert result.pullback_state == "healthy_pullback"


def test_healthy_pullback_near_sma50():
    ohlcv, indicators = make_data(close=86, recent_high=100, sma20=94, sma50=88)
    result = analyze_pullback(ohlcv, indicators, *structures())
    assert result.pullback_state == "healthy_pullback"


def test_below_sma20_without_bearish_structure_is_unstable():
    ohlcv, indicators = make_data(close=90, recent_high=100, sma20=95, sma50=85)
    result = analyze_pullback(ohlcv, indicators, *structures("bullish"))
    assert result.pullback_state == "unstable_pullback"


def test_bearish_structure_is_breakdown():
    ohlcv, indicators = make_data(close=85, recent_high=100, sma20=95, sma50=92)
    result = analyze_pullback(ohlcv, indicators, *structures("bearish"))
    assert result.pullback_state == "breakdown"


def test_extended_uptrend():
    ohlcv, indicators = make_data(close=120, recent_high=120, sma20=100, sma50=95, atr=2)
    result = analyze_pullback(ohlcv, indicators, *structures())
    assert result.pullback_state == "extended_uptrend"


def test_as_of_date_excludes_future_high():
    ohlcv, indicators = make_data(close=100, recent_high=100)
    ohlcv.loc[ohlcv.index[-1], "high"] = 150
    result = analyze_pullback(ohlcv, indicators, *structures(), as_of_date=ohlcv["date"].iloc[-2])
    assert result.recent_high < 150


def test_insufficient_data_is_unavailable():
    ohlcv, indicators = make_data(rows=10)
    result = analyze_pullback(ohlcv, indicators, *structures())
    assert result.available is False


def test_missing_columns_raise_validation_error():
    ohlcv, indicators = make_data()
    with pytest.raises(TrendIndicatorValidationError):
        analyze_pullback(ohlcv.drop(columns=["volume"]), indicators, *structures())


def test_inputs_are_not_modified():
    ohlcv, indicators = make_data(close=92, recent_high=100, sma20=93)
    original_ohlcv, original_indicators = ohlcv.copy(deep=True), indicators.copy(deep=True)
    analyze_pullback(ohlcv, indicators, *structures())
    pd.testing.assert_frame_equal(ohlcv, original_ohlcv)
    pd.testing.assert_frame_equal(indicators, original_indicators)


def test_config_changes_classification():
    ohlcv, indicators = make_data(close=97, recent_high=100, sma20=95, sma50=90)
    default = analyze_pullback(ohlcv, indicators, *structures())
    config = replace(TrendIndicatorConfig(), pullback_shallow_max_pct=0.01, pullback_healthy_min_pct=0.01)
    changed = analyze_pullback(ohlcv, indicators, *structures(), config=config)
    assert default.pullback_state != changed.pullback_state
