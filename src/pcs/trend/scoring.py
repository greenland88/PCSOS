from __future__ import annotations

from dataclasses import dataclass

from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.models import TrendIndicatorValidationError


@dataclass(frozen=True)
class TrendScoreResult:
    available: bool
    trend_score: float | None
    trend_state: str | None
    component_scores: dict[str, float]
    weighted_contributions: dict[str, float]
    positive_contributions: tuple[str, ...] = ()
    negative_contributions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def score_trend(snapshot, interpretation, config: TrendIndicatorConfig | None = None) -> TrendScoreResult:
    config = config or TrendIndicatorConfig()
    config.validate()
    if snapshot is None or interpretation is None:
        raise TrendIndicatorValidationError("snapshot and interpretation are required")
    ma = snapshot.ma_structure
    market = snapshot.market_structure
    if not ma.available or not market.available or not interpretation.available:
        warnings = list(getattr(interpretation, "warnings", ()) or ())
        if not ma.available: warnings.append("ma_structure_unavailable")
        if not market.available: warnings.append("market_structure_unavailable")
        return TrendScoreResult(False, None, None, {}, {}, warnings=tuple(dict.fromkeys(warnings)))

    scores = {
        "market_structure_score": _market_score(market, config),
        "ma_structure_score": _ma_score(ma, config),
    }
    weights = {
        "market_structure_score": config.trend_scoring_market_structure_weight,
        "ma_structure_score": config.trend_scoring_ma_structure_weight,
        "relative_strength_score": config.trend_scoring_relative_strength_weight,
        "cleanliness_score": config.trend_scoring_cleanliness_weight,
        "setup_context_score": config.trend_scoring_setup_context_weight,
    }
    optional = {
        "relative_strength_score": (snapshot.relative_strength, _rs_score),
        "cleanliness_score": (snapshot.cleanliness, _cleanliness_score),
        "setup_context_score": (snapshot.pullback, lambda result: _setup_score(result, snapshot.support, config)),
    }
    warnings = list(getattr(interpretation, "warnings", ()) or ())
    for name, (result, scorer) in optional.items():
        if result is not None and result.available:
            scores[name] = scorer(result, config) if name != "setup_context_score" else scorer(result)
        else:
            weights.pop(name)
            warnings.append(f"{name.removesuffix('_score')}_unavailable")
    total_weight = sum(weights.values())
    contributions = {name: scores[name] * weight / total_weight for name, weight in weights.items()}
    score = round(sum(contributions.values()), 6)
    state = _classify_state(snapshot, interpretation)
    positive = tuple(name for name, value in contributions.items() if value >= 15)
    negative = tuple(name for name, value in contributions.items() if value < 10)
    reasons = (f"trend_state_{state}", "available_component_weights_renormalized" if total_weight != 100 else "all_component_weights_available")
    return TrendScoreResult(True, score, state, scores, contributions, positive, negative, tuple(dict.fromkeys(warnings)), reasons)


def _market_score(market, config):
    return {"bullish": config.trend_score_market_bullish, "neutral": config.trend_score_market_neutral, "deteriorating": config.trend_score_market_deteriorating, "bearish": config.trend_score_market_bearish}.get(market.structure_state, config.trend_score_market_neutral)


def _ma_score(ma, config):
    alignment = {"bullish": config.trend_score_ma_bullish, "mostly_bullish": config.trend_score_ma_mostly_bullish, "mixed": config.trend_score_ma_mixed, "mostly_bearish": config.trend_score_ma_mostly_bearish, "bearish": config.trend_score_ma_bearish}.get(ma.ma_alignment, config.trend_score_ma_mixed)
    slopes = []
    for name in ("sma20_slope_20d", "sma50_slope_20d", "sma200_slope_20d"):
        state = getattr(getattr(ma, name, None), "slope_state", None)
        slopes.append({"rising": config.trend_score_slope_rising, "strong_rising": config.trend_score_slope_rising, "flat": config.trend_score_slope_flat, "falling": config.trend_score_slope_falling, "strong_falling": config.trend_score_slope_falling}.get(state, config.trend_score_slope_unknown))
    return (alignment + sum(slopes) / len(slopes)) / 2


def _rs_score(result, config):
    return {"strong": config.trend_score_rs_strong, "improving": config.trend_score_rs_improving, "stable": config.trend_score_rs_stable, "weakening": config.trend_score_rs_weakening, "weak": config.trend_score_rs_weak}.get(result.rs_state, config.trend_score_rs_stable)


def _cleanliness_score(result, config):
    return {"clean": config.trend_score_clean, "acceptable": config.trend_score_acceptable, "noisy": config.trend_score_noisy, "chaotic": config.trend_score_chaotic}.get(result.cleanliness_state, config.trend_score_acceptable)


def _setup_score(pullback, support, config):
    pullback_score = {"no_pullback": config.trend_score_pullback_no_pullback, "shallow_pullback": config.trend_score_pullback_shallow, "healthy_pullback": config.trend_score_pullback_healthy, "unstable_pullback": config.trend_score_pullback_unstable, "extended_uptrend": config.trend_score_pullback_extended, "breakdown": config.trend_score_pullback_breakdown}.get(pullback.pullback_state, 50.0)
    support_score = {"strong": config.trend_score_support_strong, "moderate": config.trend_score_support_moderate, "weak": config.trend_score_support_weak, "none": config.trend_score_support_none}.get(getattr(support, "support_confluence_state", None), 50.0)
    return (pullback_score + support_score) / 2


def _classify_state(snapshot, interpretation):
    market = snapshot.market_structure
    ma = snapshot.ma_structure
    pullback = snapshot.pullback
    support = snapshot.support
    rs = snapshot.relative_strength
    if interpretation.trend_health == "broken" or market.structure_state == "bearish" or (pullback.pullback_state == "breakdown" and ma.ma_alignment in {"bearish", "mostly_bearish"}):
        return "E"
    if interpretation.trend_direction == "neutral" and market.structure_state != "bullish":
        return "D"
    if interpretation.trend_direction == "bullish":
        if pullback.pullback_state == "healthy_pullback" and support.support_confluence_state in {"moderate", "strong"} and market.structure_state == "bullish":
            return "B"
        if pullback.pullback_state == "extended_uptrend":
            return "C"
        deterioration = sum([
            interpretation.trend_health in {"mixed", "weakening"},
            pullback.pullback_state in {"unstable_pullback", "extended_uptrend"},
            snapshot.cleanliness.cleanliness_state == "chaotic",
            rs.rs_state in {"weakening", "weak"},
            getattr(ma.sma50_slope_20d, "slope_state", None) in {"falling", "strong_falling"},
        ])
        if deterioration >= 2:
            return "C"
        return "A"
    return "D"
