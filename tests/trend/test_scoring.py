from dataclasses import replace
from types import SimpleNamespace

import pytest

from pcs.trend import TrendIndicatorConfig, score_trend


def slope(state): return SimpleNamespace(slope_state=state)


def fixture(structure="bullish", alignment="bullish", rs="stable", cleanliness="acceptable", pullback="shallow_pullback", support="weak", available=True):
    ma = SimpleNamespace(available=available, ma_alignment=alignment, sma20_slope_20d=slope("rising"), sma50_slope_20d=slope("rising"), sma200_slope_20d=slope("rising"))
    market = SimpleNamespace(available=available, structure_state=structure)
    snap = SimpleNamespace(ma_structure=ma, market_structure=market, relative_strength=SimpleNamespace(available=available, rs_state=rs), cleanliness=SimpleNamespace(available=available, cleanliness_state=cleanliness), pullback=SimpleNamespace(available=available, pullback_state=pullback), support=SimpleNamespace(available=available, support_confluence_state=support))
    interp = SimpleNamespace(available=available, trend_health="strong" if structure == "bullish" else "broken" if structure == "bearish" else "mixed", trend_direction="bullish" if structure == "bullish" else "bearish" if structure == "bearish" else "neutral", warnings=())
    return snap, interp


def test_strong_clean_bullish_is_a():
    snap, interp = fixture(rs="strong", cleanliness="clean", pullback="no_pullback", support="moderate")
    result = score_trend(snap, interp)
    assert result.available and result.trend_state == "A" and result.trend_score > 80


def test_healthy_pullback_with_support_is_b():
    snap, interp = fixture(pullback="healthy_pullback", support="strong")
    assert score_trend(snap, interp).trend_state == "B"


def test_extended_or_deteriorating_is_c():
    snap, interp = fixture(pullback="extended_uptrend", cleanliness="noisy")
    assert score_trend(snap, interp).trend_state == "C"


def test_sideways_is_d():
    snap, interp = fixture(structure="neutral", alignment="mixed")
    assert score_trend(snap, interp).trend_state == "D"


def test_bearish_is_e():
    snap, interp = fixture(structure="bearish", alignment="bearish", pullback="breakdown")
    assert score_trend(snap, interp).trend_state == "E"


def test_noisy_alone_does_not_make_a_c():
    snap, interp = fixture(cleanliness="noisy", pullback="shallow_pullback")
    assert score_trend(snap, interp).trend_state == "A"


def test_weak_support_and_shallow_pullback_do_not_make_b():
    snap, interp = fixture(pullback="shallow_pullback", support="weak")
    assert score_trend(snap, interp).trend_state == "A"


def test_component_math_and_weighted_sum():
    snap, interp = fixture()
    result = score_trend(snap, interp)
    assert result.trend_score == pytest.approx(sum(result.weighted_contributions.values()))
    assert set(result.component_scores) == {"market_structure_score", "ma_structure_score", "relative_strength_score", "cleanliness_score", "setup_context_score"}


def test_weights_must_sum_to_100():
    with pytest.raises(ValueError):
        score_trend(*fixture(), replace(TrendIndicatorConfig(), trend_scoring_market_structure_weight=31))


def test_weight_change_changes_score():
    snap, interp = fixture()
    snap.ma_structure.ma_alignment = "mixed"
    default = score_trend(snap, interp)
    changed = score_trend(snap, interp, replace(TrendIndicatorConfig(), trend_scoring_market_structure_weight=40, trend_scoring_ma_structure_weight=15))
    assert changed.trend_score != default.trend_score


def test_secondary_unavailable_renormalizes_weights():
    snap, interp = fixture()
    snap.cleanliness.available = False
    result = score_trend(snap, interp)
    assert result.available is True
    assert "cleanliness_unavailable" in result.warnings


def test_key_unavailable_is_unavailable():
    snap, interp = fixture(available=False)
    assert score_trend(snap, interp).available is False
