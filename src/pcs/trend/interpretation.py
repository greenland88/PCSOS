from __future__ import annotations

from dataclasses import dataclass, field

from pcs.trend.models import TrendIndicatorValidationError


@dataclass(frozen=True)
class TrendInterpretationResult:
    available: bool
    trend_health: str | None
    trend_direction: str | None
    trend_quality: str | None
    setup_context: str | None
    positive_factors: tuple[str, ...] = ()
    negative_factors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def interpret_trend(snapshot, config=None) -> TrendInterpretationResult:
    del config  # Reserved for future explanation thresholds; no score is used here.
    if snapshot is None:
        raise TrendIndicatorValidationError("snapshot is required")
    ma = getattr(snapshot, "ma_structure", None)
    market = getattr(snapshot, "market_structure", None)
    if ma is None or market is None:
        raise TrendIndicatorValidationError("snapshot must contain MA and market structure results")
    warnings = list(getattr(snapshot, "warnings", ()) or ())
    if not getattr(ma, "available", False):
        warnings.append("ma_structure_unavailable")
    if not getattr(market, "available", False):
        warnings.append("market_structure_unavailable")
    if not getattr(ma, "available", False) or not getattr(market, "available", False):
        return TrendInterpretationResult(False, None, None, None, None, warnings=tuple(dict.fromkeys(warnings)))

    cleanliness = getattr(snapshot, "cleanliness", None)
    relative = getattr(snapshot, "relative_strength", None)
    pullback = getattr(snapshot, "pullback", None)
    support = getattr(snapshot, "support", None)
    positive, negative = _factors(ma, market, relative, cleanliness, pullback, support)
    direction = _direction(ma, market)
    health = _health(ma, market, relative, cleanliness, pullback)
    quality = getattr(cleanliness, "cleanliness_state", None) if getattr(cleanliness, "available", False) else None
    setup = _setup_context(pullback)
    if cleanliness is not None and not getattr(cleanliness, "available", False):
        warnings.append("cleanliness_unavailable")
    if relative is not None and not getattr(relative, "available", False):
        warnings.append("relative_strength_unavailable")
    if pullback is not None and not getattr(pullback, "available", False):
        warnings.append("pullback_unavailable")
    if support is not None and not getattr(support, "available", False):
        warnings.append("support_unavailable")
    reasons = _reasons(health, direction, positive, negative)
    return TrendInterpretationResult(
        True, health, direction, quality, setup,
        tuple(positive), tuple(negative), tuple(dict.fromkeys(warnings)), tuple(reasons)
    )


def _direction(ma, market):
    if market.structure_state == "bullish":
        return "bullish"
    if market.structure_state == "bearish":
        return "bearish"
    if ma.ma_alignment in {"bullish", "mostly_bullish"}:
        return "bullish"
    if ma.ma_alignment in {"bearish", "mostly_bearish"}:
        return "bearish"
    return "neutral"


def _health(ma, market, relative, cleanliness, pullback):
    rs = getattr(relative, "rs_state", None)
    quality = getattr(cleanliness, "cleanliness_state", None)
    structure = market.structure_state
    alignment = ma.ma_alignment
    mid_weak = _mid_term_weakness(ma)
    rs_weak = rs in {"weak", "weakening"}
    if structure == "bearish":
        if alignment in {"bearish", "mostly_bearish"} or getattr(pullback, "pullback_state", None) == "breakdown":
            return "broken"
        return "weakening"
    if structure == "deteriorating":
        return "weakening" if (rs_weak or alignment in {"bearish", "mostly_bearish"}) else "mixed"
    if structure == "bullish":
        if alignment == "bullish" and rs in {"strong", "improving", "stable"} and quality in {"clean", "acceptable"} and not mid_weak:
            return "strong"
        if mid_weak and rs_weak:
            return "mixed"
        return "healthy"
    if rs == "weak" or alignment in {"bearish", "mostly_bearish"}:
        return "weakening"
    return "mixed"


def _mid_term_weakness(ma):
    slope = getattr(getattr(ma, "sma50_slope_20d", None), "slope_state", None)
    return slope in {"falling", "strong_falling"}


def _factors(ma, market, relative, cleanliness, pullback, support):
    positive, negative = [], []
    if market.structure_state == "bullish":
        positive.append("bullish_market_structure")
    elif market.structure_state in {"deteriorating", "bearish"}:
        negative.append(f"{market.structure_state}_market_structure")
    if ma.ma_alignment == "bullish":
        positive.append("bullish_ma_alignment")
    elif ma.ma_alignment in {"mostly_bullish", "mixed"}:
        positive.append(f"{ma.ma_alignment}_ma_alignment")
    else:
        negative.append(f"{ma.ma_alignment}_ma_alignment")
    for name in ("sma20_slope_5d", "sma20_slope_10d", "sma200_slope_20d", "sma200_slope_40d"):
        if getattr(getattr(ma, name, None), "slope_state", None) in {"rising", "strong_rising"}:
            positive.append(f"rising_{name.replace('_slope_', '_')}")
    if _mid_term_weakness(ma):
        negative.append("sma50_20d_falling")
    rs = getattr(relative, "rs_state", None)
    if rs in {"strong", "improving"}:
        positive.append(f"rs_{rs}")
    elif rs == "stable":
        positive.append("rs_stable")
    elif rs in {"weakening", "weak"}:
        negative.append(f"rs_{rs}")
    quality = getattr(cleanliness, "cleanliness_state", None)
    if quality in {"clean", "acceptable"}:
        positive.append(f"trend_cleanliness_{quality}")
    elif quality in {"noisy", "chaotic"}:
        negative.append(f"trend_cleanliness_{quality}")
    pullback_state = getattr(pullback, "pullback_state", None)
    if pullback_state in {"shallow_pullback", "unstable_pullback", "breakdown"}:
        negative.append(pullback_state)
    if getattr(support, "support_confluence_state", None) in {"none", "weak"}:
        negative.append(f"{support.support_confluence_state}_support")
    elif getattr(support, "support_confluence_state", None) in {"moderate", "strong"}:
        positive.append(f"{support.support_confluence_state}_support")
    return positive, negative


def _setup_context(pullback):
    mapping = {
        "extended_uptrend": "extended",
        "shallow_pullback": "shallow_pullback",
        "healthy_pullback": "healthy_pullback",
        "unstable_pullback": "unstable_pullback",
        "breakdown": "breakdown",
    }
    return mapping.get(getattr(pullback, "pullback_state", None), "neutral")


def _reasons(health, direction, positive, negative):
    reasons = [f"direction_{direction}", f"health_{health}"]
    if positive:
        reasons.append("positive_factors_present")
    if negative:
        reasons.append("negative_factors_present")
    return reasons
