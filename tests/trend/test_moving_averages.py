from dataclasses import replace

import pandas as pd
import pytest

from pcs.trend import TrendIndicatorConfig, TrendIndicatorValidationError, analyze_ma_structure


def make_structure(close=110.0, sma20=100.0, sma50=90.0, sma200=80.0, rows=260):
    index = pd.date_range("2025-01-01", periods=rows, freq="D")
    return pd.DataFrame({"close": close, "sma20": sma20, "sma50": sma50, "sma200": sma200}, index=index, dtype=float)


def rising_structure(rows=260):
    df = make_structure(rows=rows)
    for col, start in [("close", 110), ("sma20", 100), ("sma50", 90), ("sma200", 80)]:
        df[col] = pd.Series(range(start, start + rows), index=df.index, dtype=float)
    return df


def test_bullish_alignment_and_slopes():
    result = analyze_ma_structure(rising_structure())
    assert result.available is True
    assert result.ma_alignment == "bullish"
    assert result.price_above_sma20 and result.sma20_above_sma50 and result.sma50_above_sma200
    assert result.sma20_slope_5d.raw_slope == 1.0
    assert result.sma20_slope_5d.normalized_daily_slope == result.sma20_slope_5d.normalized_change / 5


@pytest.mark.parametrize(("changes", "alignment"), [
    ({"close": 95}, "mostly_bullish"),
    ({"close": 85, "sma20": 90, "sma50": 100}, "mostly_bearish"),
    ({"close": 70, "sma20": 80, "sma50": 90, "sma200": 100}, "bearish"),
    ({"close": 85, "sma20": 95, "sma50": 90}, "mixed"),
])
def test_alignment_cases(changes, alignment):
    values = {"close": 110.0, "sma20": 100.0, "sma50": 90.0, "sma200": 80.0}
    values.update(changes)
    assert analyze_ma_structure(make_structure(**values)).ma_alignment == alignment


def test_slope_can_turn_down_while_medium_ma_rises():
    df = rising_structure()
    df.loc[df.index[-21:], "sma20"] = list(range(300, 279, -1))
    result = analyze_ma_structure(df)
    assert result.sma20_slope_20d.slope_state in {"falling", "strong_falling"}
    assert result.sma50_slope_20d.slope_state in {"rising", "strong_rising"}


def test_insufficient_lookback_is_unavailable():
    result = analyze_ma_structure(make_structure(rows=10))
    assert result.available is False
    assert result.ma_alignment is None
    assert result.sma20_slope_20d.raw_slope is None
    assert result.sma20_slope_20d.slope_state is None


def test_missing_columns_raise_validation_error():
    with pytest.raises(TrendIndicatorValidationError):
        analyze_ma_structure(make_structure().drop(columns=["sma200"]))


def test_input_is_not_modified():
    df = rising_structure()
    original = df.copy(deep=True)
    analyze_ma_structure(df)
    pd.testing.assert_frame_equal(df, original)


def test_thresholds_are_configurable():
    config = replace(
        TrendIndicatorConfig(),
        slope_strong_threshold=0.6,
        slope_rising_threshold=0.5,
        slope_flat_threshold=0.5,
    )
    result = analyze_ma_structure(rising_structure(), config)
    assert result.sma20_slope_5d.slope_state == "flat"
