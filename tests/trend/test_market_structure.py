from dataclasses import replace

import pandas as pd
import pytest

from pcs.trend import TrendIndicatorConfig, TrendIndicatorValidationError, analyze_market_structure


def make_ohlcv(highs, lows, closes=None):
    dates = pd.date_range("2025-01-01", periods=len(highs), freq="D")
    highs = pd.Series(highs, index=dates, dtype=float)
    lows = pd.Series(lows, index=dates, dtype=float)
    closes = highs if closes is None else pd.Series(closes, index=dates, dtype=float)
    return pd.DataFrame({"date": dates, "open": closes, "high": highs, "low": lows, "close": closes, "volume": 1_000_000}, index=dates)


def config():
    return TrendIndicatorConfig(pivot_left_bars=1, pivot_right_bars=1, minimum_swing_price_change_pct=0.01)


def test_higher_high_and_higher_low_is_bullish():
    df = make_ohlcv([10, 20, 11, 25, 12, 30, 13], [5, 8, 6, 10, 7, 12, 9])
    result = analyze_market_structure(df, config())
    assert result.available and result.higher_high and result.higher_low
    assert result.structure_state == "bullish"


def test_lower_high_and_lower_low_is_bearish():
    df = make_ohlcv([30, 25, 29, 20, 28, 15, 27], [20, 18, 19, 14, 17, 10, 16])
    result = analyze_market_structure(df, config())
    assert result.available and result.lower_high and result.lower_low
    assert result.structure_state == "bearish"


def test_mixed_structures_are_neutral_or_deteriorating():
    hh_ll = analyze_market_structure(make_ohlcv([10, 20, 11, 15, 12, 25, 13], [5, 8, 6, 10, 7, 4, 9]), config())
    lh_hl = analyze_market_structure(make_ohlcv([30, 25, 29, 20, 28, 24, 27], [20, 18, 19, 14, 17, 15, 18]), config())
    assert hh_ll.structure_state == "neutral"
    assert lh_hl.structure_state == "deteriorating"


def test_approximately_equal_highs_and_lows_are_not_classified():
    df = make_ohlcv([10, 20, 11, 20.1, 12], [5, 8, 6, 8.04, 7])
    result = analyze_market_structure(df, config())
    assert result.higher_high is None
    assert result.lower_high is None
    assert result.higher_low is None
    assert result.lower_low is None


def test_pivot_is_not_confirmed_before_right_bars():
    df = make_ohlcv([10, 20, 11, 25, 12], [5, 8, 6, 10, 7])
    result = analyze_market_structure(df, config())
    assert all(s.confirmed_at > s.pivot_date for s in result.confirmed_swings)
    assert not any(s.pivot_date == df.index[-1] for s in result.confirmed_swings)


def test_as_of_date_cannot_see_future_confirmed_pivot():
    df = make_ohlcv([10, 20, 11, 25, 12, 30, 13], [5, 8, 6, 10, 7, 12, 9])
    full = analyze_market_structure(df, config())
    historical = analyze_market_structure(df, config(), as_of_date=df.index[3])
    assert full.confirmed_swings
    assert all(s.confirmed_at <= df.index[3] for s in historical.confirmed_swings)
    assert not any(s.pivot_date == df.index[5] for s in historical.confirmed_swings)


def test_insufficient_data_is_unavailable():
    result = analyze_market_structure(make_ohlcv([1, 2], [0, 1]), config())
    assert result.available is False
    assert result.structure_state is None


def test_missing_columns_raise_validation_error():
    with pytest.raises(TrendIndicatorValidationError):
        analyze_market_structure(make_ohlcv([1, 2, 1], [0, 1, 0]).drop(columns=["volume"]), config())


def test_input_is_not_modified():
    df = make_ohlcv([10, 20, 11, 25, 12], [5, 8, 6, 10, 7])
    original = df.copy(deep=True)
    analyze_market_structure(df, config())
    pd.testing.assert_frame_equal(df, original)


def test_pivot_parameter_changes_results():
    df = make_ohlcv([10, 20, 11, 25, 12, 30, 13], [5, 8, 6, 10, 7, 12, 9])
    one_bar = analyze_market_structure(df, config())
    three_bars = analyze_market_structure(df, replace(config(), pivot_left_bars=3, pivot_right_bars=3))
    assert one_bar.confirmed_swings != three_bars.confirmed_swings
