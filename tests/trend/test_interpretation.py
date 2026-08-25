from types import SimpleNamespace

from pcs.trend import TrendInterpretationResult, interpret_trend


def slope(state):
    return SimpleNamespace(slope_state=state)


def snapshot(
    structure="bullish", alignment="bullish", rs="stable", cleanliness="acceptable",
    pullback="neutral", support="moderate", ma_available=True, market_available=True,
    cleanliness_available=True, support_available=True,
):
    ma = SimpleNamespace(
        available=ma_available, ma_alignment=alignment,
        sma20_slope_5d=slope("rising"), sma20_slope_10d=slope("rising"),
        sma20_slope_20d=slope("rising"), sma50_slope_10d=slope("flat"),
        sma50_slope_20d=slope("rising"), sma200_slope_20d=slope("rising"),
        sma200_slope_40d=slope("rising"),
    )
    market = SimpleNamespace(available=market_available, structure_state=structure)
    return SimpleNamespace(
        ma_structure=ma, market_structure=market,
        relative_strength=SimpleNamespace(available=True, rs_state=rs),
        cleanliness=SimpleNamespace(available=cleanliness_available, cleanliness_state=cleanliness),
        pullback=SimpleNamespace(available=True, pullback_state=pullback),
        support=SimpleNamespace(available=support_available, support_confluence_state=support),
        warnings=(),
    )


def test_strong_snapshot():
    result = interpret_trend(snapshot(rs="strong", cleanliness="clean"))
    assert result.trend_health == "strong"
    assert result.trend_direction == "bullish"
    assert result.trend_quality == "clean"


def test_noisy_bullish_structure_is_healthy_not_broken():
    result = interpret_trend(snapshot(cleanliness="noisy"))
    assert result.trend_health == "healthy"
    assert result.trend_direction == "bullish"


def test_weakening_rs_and_falling_ma50_is_mixed():
    value = snapshot(rs="weakening")
    value.ma_structure.sma50_slope_20d = slope("falling")
    result = interpret_trend(value)
    assert result.trend_health == "mixed"


def test_deteriorating_structure_and_weak_rs_is_weakening():
    result = interpret_trend(snapshot(structure="deteriorating", rs="weak"))
    assert result.trend_health == "weakening"


def test_bearish_structure_and_ma_is_broken():
    result = interpret_trend(snapshot(structure="bearish", alignment="bearish"))
    assert result.trend_health == "broken"
    assert result.trend_direction == "bearish"


def test_noisy_does_not_make_bullish_structure_broken():
    result = interpret_trend(snapshot(cleanliness="chaotic"))
    assert result.trend_health != "broken"


def test_weak_support_and_shallow_pullback_do_not_lower_health():
    result = interpret_trend(snapshot(pullback="shallow_pullback", support="weak"))
    assert result.trend_health in {"strong", "healthy"}
    assert "shallow_pullback" in result.negative_factors
    assert "weak_support" in result.negative_factors


def test_support_unavailable_still_interprets():
    result = interpret_trend(snapshot(support_available=False))
    assert result.available is True
    assert "support_unavailable" in result.warnings


def test_required_structure_unavailable_makes_interpretation_unavailable():
    assert interpret_trend(snapshot(market_available=False)).available is False
    assert interpret_trend(snapshot(ma_available=False)).available is False


def test_snapshot_is_not_modified():
    value = snapshot()
    before = repr(value)
    interpret_trend(value)
    assert repr(value) == before
