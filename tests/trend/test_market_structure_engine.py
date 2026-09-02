from types import SimpleNamespace
import pandas as pd

from pcs.trend.market_structure_engine import build_market_structure_engine


def _snapshot(state="bearish", above20=True, alignment="mixed", slope="falling", hh=False, hl=False, lh=True, ll=True):
    return SimpleNamespace(
        available=True,
        ma_structure=SimpleNamespace(ma_alignment=alignment, price_above_sma20=above20,
                                     sma20_slope_20d=SimpleNamespace(slope_state=slope),
                                     sma50_slope_20d=SimpleNamespace(slope_state=slope)),
        market_structure=SimpleNamespace(structure_state=state, higher_high=hh,
                                         higher_low=hl, lower_high=lh, lower_low=ll),
        pullback=SimpleNamespace(pullback_state="shallow_pullback"),
    )


def _ohlcv():
    dates = pd.date_range("2026-01-01", periods=80, freq="D")
    return pd.DataFrame({"date": dates, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0})


def test_lower_high_lower_low_is_structural_downtrend_even_above_ma20():
    result = build_market_structure_engine(_snapshot(), _ohlcv(), "2026-03-21")
    assert result.structural_trend == "STRUCTURAL_DOWNTREND"
    assert result.short_term_phase == "SUPPORT_BREAKDOWN"
    assert "lower_high_lower_low_combination" in result.reasons


def test_pit_feature_max_date_is_not_after_signal_date():
    result = build_market_structure_engine(_snapshot(), _ohlcv(), "2026-02-15")
    assert pd.Timestamp(result.feature_max_date) <= pd.Timestamp("2026-02-15")
