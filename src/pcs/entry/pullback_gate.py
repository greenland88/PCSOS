from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PullbackGateResult:
    available: bool
    pullback_gate_result: str | None
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def evaluate_pullback_gate(trend_gate, snapshot, interpretation) -> PullbackGateResult:
    warnings = list(getattr(trend_gate, "warnings", ()) or ())
    warnings.extend(getattr(interpretation, "warnings", ()) or ())
    missing = []
    for name, result in (("trend_gate", trend_gate), ("snapshot", snapshot), ("interpretation", interpretation)):
        if result is None or not getattr(result, "available", False):
            missing.append(f"{name}_unavailable")
    if missing:
        warnings.extend(missing)
        return PullbackGateResult(False, None, warnings=tuple(dict.fromkeys(warnings)))

    gate = trend_gate.trend_gate_result
    pullback = snapshot.pullback
    support = snapshot.support
    setup = interpretation.setup_context
    health = interpretation.trend_health
    direction = interpretation.trend_direction
    market_state = snapshot.market_structure.structure_state
    pullback_state = pullback.pullback_state
    support_state = support.support_confluence_state

    if gate == "REJECT":
        return _result("REJECT", ("trend_gate_reject",), warnings)
    if setup == "breakdown" or pullback_state == "breakdown":
        return _result("REJECT", ("breakdown_context",), warnings)
    if direction == "bearish":
        return _result("REJECT", ("trend_direction_bearish",), warnings)
    if health == "broken":
        return _result("REJECT", ("trend_health_broken",), warnings)
    if gate != "PASS":
        return _result("WAIT", ("trend_gate_not_pass",), warnings)
    if pullback_state == "unstable_pullback":
        if health == "weakening" or market_state == "deteriorating":
            return _result("REJECT", ("unstable_pullback", "trend_weakening"), warnings)
        return _result("WAIT", ("unstable_pullback", "pullback_needs_stabilization"), warnings)

    if (
        pullback_state == "healthy_pullback"
        and support_state in {"moderate", "strong"}
        and market_state == "bullish"
        and health in {"strong", "healthy"}
    ):
        return _result("PASS", ("trend_gate_pass", "healthy_pullback", "bullish_market_structure", "support_confluence_sufficient", "healthy_trend"), warnings)

    reasons = ["trend_gate_pass"]
    if pullback_state:
        reasons.append(pullback_state)
    if support_state:
        reasons.append(f"{support_state}_support")
    reasons.append("waiting_for_qualified_pullback")
    return _result("WAIT", tuple(reasons), warnings)


def _result(result, reasons, warnings):
    return PullbackGateResult(True, result, tuple(reasons), tuple(dict.fromkeys(warnings)))
