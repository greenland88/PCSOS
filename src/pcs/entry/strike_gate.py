from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from pcs.trend.config import TrendIndicatorConfig


@dataclass(frozen=True)
class StrikeGateResult:
    available: bool
    strike_gate_result: str | None
    current_close: float | None = None
    short_strike: float | None = None
    strike_buffer_pct: float | None = None
    strike_buffer_atr: float | None = None
    distance_below_nearest_support_pct: float | None = None
    distance_below_nearest_support_atr: float | None = None
    normal_move_buffer_3d_atr: float | None = None
    normal_move_buffer_5d_atr: float | None = None
    strike_buffer_vs_3d_normal_move: float | None = None
    strike_buffer_vs_5d_normal_move: float | None = None
    safe_strike_reference: float | None = None
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def evaluate_short_strike(short_strike, snapshot, interpretation, trend_gate, pullback_gate, config=None):
    config = config or TrendIndicatorConfig()
    config.validate()
    warnings = list(getattr(trend_gate, "warnings", ()) or ())
    warnings.extend(getattr(pullback_gate, "warnings", ()) or ())
    if snapshot is None or interpretation is None or trend_gate is None or pullback_gate is None:
        return _unavailable(warnings + ["required_input_unavailable"])
    if not all(getattr(value, "available", False) for value in (snapshot, interpretation, trend_gate, pullback_gate)):
        return _unavailable(warnings + ["required_input_unavailable"])
    if short_strike is None:
        return _unavailable(warnings + ["short_strike_unavailable"])
    try:
        strike = float(short_strike)
        close = float(snapshot.current_close if hasattr(snapshot, "current_close") else snapshot.pullback.current_close)
        atr = float(snapshot.current_atr if hasattr(snapshot, "current_atr") else snapshot.support.current_atr)
    except (AttributeError, TypeError, ValueError):
        return _unavailable(warnings + ["price_or_atr_unavailable"])
    if atr <= 0 or close <= 0:
        return _unavailable(warnings + ["price_or_atr_invalid"])

    buffer_pct = (close - strike) / close
    buffer_atr = (close - strike) / atr
    normal3 = config.strike_normal_move_3d_multiplier
    normal5 = config.strike_normal_move_5d_multiplier
    coverage3 = buffer_atr / normal3
    coverage5 = buffer_atr / normal5
    support = getattr(snapshot, "support", None)
    nearest = getattr(support, "nearest_support", None)
    support_state = getattr(support, "support_confluence_state", None)
    distance_support_pct = distance_support_atr = None
    if nearest is not None:
        distance_support_pct = (float(nearest) - strike) / close
        distance_support_atr = (float(nearest) - strike) / atr
    references = [close - normal5 * atr, close - normal3 * atr]
    if nearest is not None and support_state in {"moderate", "strong"}:
        references.append(float(nearest) - config.strike_support_buffer_atr_pass * atr)
    safe_reference = min(references)
    if trend_gate.trend_gate_result == "REJECT" or pullback_gate.pullback_gate_result == "REJECT":
        return _calculated_result("REJECT", close, strike, buffer_pct, buffer_atr, distance_support_pct, distance_support_atr, normal3, normal5, coverage3, coverage5, safe_reference, ("entry_context_reject",), warnings)

    reasons = []
    if pullback_gate.pullback_gate_result == "WAIT":
        reasons.append("pullback_gate_wait")
    if nearest is not None and distance_support_atr < 0:
        return _calculated_result("REJECT", close, strike, buffer_pct, buffer_atr, distance_support_pct, distance_support_atr, normal3, normal5, coverage3, coverage5, safe_reference, tuple(reasons + ["strike_above_nearest_support"]), warnings)
    if coverage3 < config.strike_min_3d_coverage_marginal or buffer_atr < config.strike_min_buffer_atr_marginal:
        return _calculated_result("REJECT", close, strike, buffer_pct, buffer_atr, distance_support_pct, distance_support_atr, normal3, normal5, coverage3, coverage5, safe_reference, tuple(reasons + ["normal_3d_buffer_insufficient"]), warnings)
    if buffer_atr >= config.strike_min_buffer_atr_pass and coverage3 >= config.strike_min_3d_coverage_pass and coverage5 >= config.strike_min_5d_coverage_pass and (nearest is None or support_state == "weak" or distance_support_atr >= config.strike_support_buffer_atr_pass):
        reasons.extend(["normal_3d_buffer_covered", "normal_5d_buffer_covered"])
        if nearest is not None and distance_support_atr >= config.strike_support_buffer_atr_pass:
            reasons.append("strike_below_support_buffer")
        return _calculated_result("PASS", close, strike, buffer_pct, buffer_atr, distance_support_pct, distance_support_atr, normal3, normal5, coverage3, coverage5, safe_reference, tuple(reasons), warnings)
    return _calculated_result("MARGINAL", close, strike, buffer_pct, buffer_atr, distance_support_pct, distance_support_atr, normal3, normal5, coverage3, coverage5, safe_reference, tuple(reasons + ["buffer_only_partially_covers_normal_move"]), warnings)


def _calculated_result(result, close, strike, buffer_pct, buffer_atr, distance_pct, distance_atr, normal3, normal5, coverage3, coverage5, reference, reasons, warnings):
    return StrikeGateResult(True, result, close, strike, buffer_pct, buffer_atr, distance_pct, distance_atr, normal3, normal5, coverage3, coverage5, reference, tuple(reasons), tuple(dict.fromkeys(warnings)))


def _unavailable(warnings):
    return StrikeGateResult(False, None, warnings=tuple(dict.fromkeys(warnings)))
