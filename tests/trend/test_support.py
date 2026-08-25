from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from pcs.trend import TrendIndicatorConfig, TrendIndicatorValidationError, analyze_support


def make_data(close=100.0, sma20=98.0, sma50=95.0, atr=2.0, rows=60):
    dates = pd.date_range("2025-01-01", periods=rows, freq="D")
    close_values = np.full(rows, close, dtype=float)
    ohlcv = pd.DataFrame({"date": dates, "open": close_values, "high": close_values + 1, "low": close_values - 1, "close": close_values, "volume": 1_000_000})
    indicators = pd.DataFrame({"sma20": np.full(rows, sma20), "sma50": np.full(rows, sma50), "atr14": np.full(rows, atr)})
    return ohlcv, indicators


def market_structure(swings=(), state="bullish"):
    return SimpleNamespace(confirmed_swings=tuple(swings), structure_state=state)


def swing(price, swing_type="low", confirmed_at="2025-02-20"):
    return SimpleNamespace(price=price, swing_type=swing_type, confirmed_at=pd.Timestamp(confirmed_at), pivot_date=pd.Timestamp(confirmed_at))


def test_price_near_sma20_is_weak_support():
    ohlcv, indicators = make_data(close=100, sma20=99, sma50=90)
    result = analyze_support(ohlcv, indicators, market_structure())
    assert result.support_confluence_state == "weak"
    assert result.nearest_support_type == "sma20"


def test_price_near_sma50_is_weak_support():
    ohlcv, indicators = make_data(close=100, sma20=110, sma50=99)
    result = analyze_support(ohlcv, indicators, market_structure())
    assert result.nearest_support_type == "sma50"
    assert result.support_confluence_state == "weak"


def test_price_near_confirmed_swing_low():
    ohlcv, indicators = make_data(close=100, sma20=90, sma50=80)
    result = analyze_support(ohlcv, indicators, market_structure([swing(99)]))
    assert result.nearest_support_type == "latest_swing_low"


def test_sma50_and_swing_low_form_cluster():
    ohlcv, indicators = make_data(close=100, sma20=90, sma50=98, atr=2)
    result = analyze_support(ohlcv, indicators, market_structure([swing(98.3)]))
    assert result.support_confluence_state == "moderate"
    assert result.support_clusters[0]["strength"] == 2


def test_distant_supports_are_not_strong_confluence():
    ohlcv, indicators = make_data(close=100, sma20=80, sma50=70, atr=2)
    result = analyze_support(ohlcv, indicators, market_structure([swing(60)]))
    assert result.support_confluence_state == "none"
    assert result.support_count_nearby == 0


def test_price_below_all_supports_has_no_active_support():
    ohlcv, indicators = make_data(close=80, sma20=100, sma50=95, atr=2)
    result = analyze_support(ohlcv, indicators, market_structure([swing(90)]))
    assert result.support_confluence_state == "none"
    assert result.nearest_support is None


def test_as_of_date_excludes_future_swing():
    ohlcv, indicators = make_data()
    future = swing(99, confirmed_at="2025-03-01")
    result = analyze_support(ohlcv, indicators, market_structure([future]), as_of_date="2025-02-20")
    assert all(item["type"] != "latest_swing_low" for item in result.supports)


def test_insufficient_data_is_unavailable():
    ohlcv, indicators = make_data(rows=10)
    config = replace(TrendIndicatorConfig(), pullback_recent_high_lookback=20)
    assert analyze_support(ohlcv, indicators, market_structure(), config).available is False


def test_missing_columns_raise_validation_error():
    ohlcv, indicators = make_data()
    with pytest.raises(TrendIndicatorValidationError):
        analyze_support(ohlcv.drop(columns=["volume"]), indicators, market_structure())


def test_inputs_are_not_modified():
    ohlcv, indicators = make_data()
    original_ohlcv, original_indicators = ohlcv.copy(deep=True), indicators.copy(deep=True)
    analyze_support(ohlcv, indicators, market_structure([swing(99)]))
    pd.testing.assert_frame_equal(ohlcv, original_ohlcv)
    pd.testing.assert_frame_equal(indicators, original_indicators)


def test_config_changes_nearby_result():
    ohlcv, indicators = make_data(close=100, sma20=97, sma50=90, atr=2)
    default = analyze_support(ohlcv, indicators, market_structure())
    strict = analyze_support(ohlcv, indicators, market_structure(), replace(TrendIndicatorConfig(), support_nearby_atr=0.5, support_nearby_pct=0.005))
    assert default.support_confluence_state != strict.support_confluence_state
