from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrendGateResult:
    available: bool
    trend_gate_result: str | None
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def evaluate_trend_gate(trend_score, interpretation, snapshot) -> TrendGateResult:
    """Evaluate trend eligibility for the next PCS screening layer only."""
    warnings = list(getattr(trend_score, "warnings", ()) or ())
    warnings.extend(getattr(interpretation, "warnings", ()) or ())
    unavailable = []
    for name, result in (
        ("trend_score", trend_score),
        ("interpretation", interpretation),
        ("snapshot", snapshot),
    ):
        if result is None or not getattr(result, "available", False):
            unavailable.append(f"{name}_unavailable")
    if unavailable:
        warnings.extend(unavailable)
        return TrendGateResult(False, None, warnings=tuple(dict.fromkeys(warnings)))

    state = trend_score.trend_state
    health = interpretation.trend_health
    direction = interpretation.trend_direction
    setup = interpretation.setup_context
    reasons = []

    if state == "E":
        return TrendGateResult(True, "REJECT", ("trend_state_E",), tuple(dict.fromkeys(warnings)))
    if health == "broken":
        return TrendGateResult(True, "REJECT", ("trend_health_broken",), tuple(dict.fromkeys(warnings)))
    if direction == "bearish":
        reasons.append("trend_direction_bearish")
        if health == "weakening":
            reasons.append("weakening_bearish_trend")
        return TrendGateResult(True, "REJECT", tuple(reasons), tuple(dict.fromkeys(warnings)))
    if setup == "breakdown":
        return TrendGateResult(True, "REJECT", ("setup_context_breakdown",), tuple(dict.fromkeys(warnings)))

    if state in {"A", "B"} and direction != "bearish" and health not in {"weakening", "broken"}:
        reasons = [f"trend_state_{state}", f"trend_direction_{direction}", f"trend_health_{health}"]
        if state == "B":
            reasons.append("healthy_uptrend_pullback")
        else:
            reasons.append("strong_trend_eligibility")
        return TrendGateResult(True, "PASS", tuple(reasons), tuple(dict.fromkeys(warnings)))

    if state == "C":
        reasons.append("trend_state_C")
    if state == "D":
        reasons.append("trend_state_D")
    if health == "mixed":
        reasons.append("trend_health_mixed")
    if direction == "neutral":
        reasons.append("trend_direction_neutral")
    if setup in {"extended", "unstable_pullback"}:
        reasons.append(f"setup_context_{setup}")
    if not reasons:
        reasons.append("trend_conditions_insufficient_for_pass")
    return TrendGateResult(True, "WATCH", tuple(reasons), tuple(dict.fromkeys(warnings)))
