"""Prepared-fact detectors for the v2 healthy-pullback opportunity family.

The functions in this module are deterministic and side-effect free.  They
consume indicator values and support-test facts supplied by adapters; they do
not read data or calculate an alternative ATR.
"""
from __future__ import annotations

import math
from pcs.trend.lifecycle import required_conjunction

from pcs.trend.shallow_pullback import detect_shallow_pullback

from pcs.trend.selection_models import (
    OpportunityCondition, OpportunityDetection, OpportunityFeatureBar,
    OpportunityPolicy, OpportunitySupportFact,
)


def _finite(value) -> bool:
    return value is not None and math.isfinite(float(value))


_BLOCKING_PHASES = {
    "RECLAIM_DAY_1", "FAILED_FOLLOW_THROUGH", "RECLAIM_UNCONFIRMED",
    "UPTREND_EXHAUSTION", "DISTRIBUTION", "DOWNTREND_RALLY",
    "SUPPORT_BREAKDOWN", "BREAKOUT_REJECTED",
}


def _trend_predicates(bar):
    structure_ok = None if bar.structure_state is None else bar.structure_state == "bullish"
    health_ok = None if bar.trend_health is None else str(bar.trend_health).lower() in {"strong", "healthy"}
    phase_ok = None if bar.short_term_phase is None else bar.short_term_phase not in _BLOCKING_PHASES
    return structure_ok, health_ok, phase_ok


def _condition(condition_id, session, role, left=None, operator=None, right=None,
               unit=None, predicate=None, refs=(), reasons=()):
    status = "UNKNOWN" if predicate is None else "EVALUATED"
    return OpportunityCondition(condition_id=condition_id, session=session, role=role,
        left_value=left, operator=operator, right_value=right, unit=unit,
        predicate_value=predicate, status=status, source_refs=list(refs),
        reason_codes=list(reasons))


def _legacy_classification(bar, pullback_pct, distance20, distance50, policy):
    """Mirror current pullback branch order when an adapter has no saved fact."""
    if bar.legacy_pullback_state:
        return bar.legacy_pullback_state, list(bar.legacy_pullback_reasons)
    if pullback_pct is None or distance20 is None or distance50 is None:
        return None, ["PULLBACK_FACTS_INCOMPLETE"]
    if bar.structure_state == "bearish":
        return "breakdown", ["market_structure_bearish"]
    # The old implementation tests shallow before healthy.  Thus an exact 5%
    # pullback that is above both averages remains shallow, not healthy.
    if (pullback_pct <= policy.shallow_pullback_max_pct and
            distance20 >= 0 and distance50 >= 0):
        return "shallow_pullback", ["pullback_shallow", "price_above_sma20", "price_above_sma50"]
    near20 = abs(distance20) <= policy.sma20_near_atr
    near50 = abs(distance50) <= policy.sma50_near_atr
    if (policy.healthy_pullback_min_pct <= pullback_pct <= policy.healthy_pullback_max_pct
            and (near20 or near50) and bar.structure_state != "bearish"):
        return "healthy_pullback", ["pullback_within_normal_range", "near_sma20" if near20 else "near_sma50"]
    return "unstable_pullback", ["PULLBACK_NOT_HEALTHY"]


def detect_healthy_pullback(*, bar: OpportunityFeatureBar,
                            history: list[OpportunityFeatureBar],
                            support_facts: list[OpportunitySupportFact],
                            policy: OpportunityPolicy) -> OpportunityDetection:
    """Detect a first legal touch and retain every evaluated discovery fact."""
    session = bar.session.isoformat()
    conditions: list[OpportunityCondition] = []
    prior = [b for b in history if b.session <= bar.session]
    lookback = prior[-policy.recent_high_sessions:]
    highs = [(b.session.isoformat(), b.high) for b in lookback if _finite(b.high)]
    recent_high_session, recent_high = max(highs, key=lambda x: x[1]) if len(highs) == policy.recent_high_sessions else (None, None)
    pullback_pct = ((recent_high-float(bar.close))/recent_high
                    if _finite(recent_high) and recent_high > 0 and _finite(bar.close) else None)
    valid_atr = _finite(bar.atr14) and float(bar.atr14) > 0
    distance20 = ((float(bar.close)-float(bar.sma20))/float(bar.atr14)
                  if valid_atr and _finite(bar.close) and _finite(bar.sma20) else None)
    distance50 = ((float(bar.close)-float(bar.sma50))/float(bar.atr14)
                  if valid_atr and _finite(bar.close) and _finite(bar.sma50) else None)
    classification, legacy_reasons = _legacy_classification(bar, pullback_pct, distance20, distance50, policy)

    conditions.append(_condition("PULLBACK_CLASSIFIED_HEALTHY", session, "DISCOVERY",
        classification, "==", "healthy_pullback", predicate=(classification == "healthy_pullback") if classification else None,
        refs=[f"bar:{session}", f"legacy_pullback:{session}"], reasons=legacy_reasons))
    structure_ok, trend_ok, phase_ok = _trend_predicates(bar)
    conditions.append(_condition("STRUCTURE_BULLISH", session, "DISCOVERY",
        bar.structure_state, "==", "bullish", predicate=structure_ok,
        refs=[f"structure:{session}"], reasons=["STRUCTURE_UNKNOWN"] if structure_ok is None else []))
    conditions.append(_condition("TREND_HEALTH_QUALIFIED", session, "DISCOVERY",
        bar.trend_health, "IN", "strong|healthy", predicate=trend_ok,
        refs=[x for x in (f"trend_health:{session}", bar.trend_health_source) if x],
        reasons=["TREND_HEALTH_UNKNOWN"] if trend_ok is None else []))
    conditions.append(_condition("SHORT_TERM_PHASE_NOT_BLOCKED", session, "DISCOVERY",
        bar.short_term_phase, "NOT_IN", "explicit_blocking_phases", predicate=phase_ok,
        refs=[x for x in (f"short_term_phase:{session}", bar.short_term_phase_source) if x],
        reasons=["SHORT_TERM_PHASE_UNKNOWN"] if phase_ok is None else
                [f"SHORT_TERM_PHASE_BLOCKED:{bar.short_term_phase}"] if not phase_ok else []))
    conditions.append(_condition("LEGACY_TREND_GATE_RESULT", session, "DIAGNOSTIC",
        bar.legacy_trend_gate_result, "==", "PASS",
        predicate=(bar.legacy_trend_gate_result == "PASS"
                   if bar.legacy_trend_gate_status == "EXECUTED" else None),
        refs=["pcs.entry.trend_gate.evaluate_trend_gate"],
        reasons=bar.legacy_trend_gate_reasons or [f"LEGACY_TREND_GATE_{bar.legacy_trend_gate_status}"]))
    conditions.append(_condition("LEGACY_PULLBACK_GATE_RESULT", session, "DIAGNOSTIC",
        bar.legacy_pullback_gate_result, "==", "PASS",
        predicate=(bar.legacy_pullback_gate_result == "PASS"
                   if bar.legacy_pullback_gate_status == "EXECUTED" else None),
        refs=["pcs.entry.pullback_gate.evaluate_pullback_gate"],
        reasons=bar.legacy_pullback_gate_reasons or [f"LEGACY_PULLBACK_GATE_{bar.legacy_pullback_gate_status}"]))

    legal = [f for f in support_facts if f.session == session and f.touch_session == session
             and f.zone_available_at < session and f.broken_at is None]
    legal.sort(key=lambda f: (
        abs(float(bar.close)-((f.zone_lower+f.zone_upper)/2)) if _finite(bar.close) else math.inf,
        f.zone_available_at, f.zone_id, f.test_id))
    selected = legal[0] if legal else None
    support_known = selected is not None
    conditions.append(_condition("LEGAL_SUPPORT_TOUCH", session, "DISCOVERY",
        selected.test_id if selected else None, "IS_NOT", None, predicate=support_known,
        refs=[selected.support_result_id, selected.zone_id, selected.test_id] if selected else [],
        reasons=[] if selected else ["NO_SUPPORT_ZONE_KNOWN_BEFORE_TOUCH"]))

    # Three-valued AND: one explicit false proves there is no setup even when
    # another required input is unknown; only an otherwise viable setup stays
    # unknown because of missing evidence.
    gates = [c for c in conditions if c.role == "DISCOVERY"]
    detected = required_conjunction(gates)
    reasons = ["SUPPORT_SELECTION_DISTANCE_AVAILABLE_AT_ZONE_TEST_ID_V1"] if detected else list(dict.fromkeys(
        r for c in conditions for r in c.reason_codes)) or ["HEALTHY_PULLBACK_DISCOVERY_NOT_SATISFIED"]
    return OpportunityDetection(session=session, detected=detected, family=policy.family,
        recent_high=recent_high, recent_high_session=recent_high_session,
        pullback_pct=pullback_pct, distance_sma20_atr=distance20,
        distance_sma50_atr=distance50, legacy_pullback_state=classification,
        legacy_pullback_reasons=legacy_reasons, selected_support=selected,
        alternative_supports=legal[1:], conditions=conditions, reason_codes=reasons)


def volume_ratio(bar: OpportunityFeatureBar, history: list[OpportunityFeatureBar],
                 prior_sessions: int = 20):
    earlier = [b for b in history if b.session < bar.session]
    sample = earlier[-prior_sessions:]
    if len(sample) != prior_sessions or not _finite(bar.volume):
        return None, len([b for b in sample if _finite(b.volume)]), None
    values = [float(b.volume) for b in sample if _finite(b.volume)]
    if len(values) != prior_sessions:
        return None, len(values), None
    denominator = sum(values) / prior_sessions
    if not math.isfinite(denominator) or denominator <= 0:
        return None, len(values), denominator
    return float(bar.volume) / denominator, len(values), denominator


def confirmation_conditions(*, bar: OpportunityFeatureBar,
                            previous_bar: OpportunityFeatureBar | None,
                            history: list[OpportunityFeatureBar],
                            zone_upper: float, anchor_atr: float, zone_id: str,
                            test_id: str, support_fact: OpportunitySupportFact | None,
                            policy: OpportunityPolicy) -> list[OpportunityCondition]:
    session = bar.session.isoformat()
    refs = [f"bar:{session}", zone_id, test_id]
    valid_atr = _finite(bar.atr14) and float(bar.atr14) > 0
    frozen_atr_ok = _finite(anchor_atr) and anchor_atr > 0
    reclaim_line = zone_upper + policy.reclaim_buffer_atr*anchor_atr if frozen_atr_ok else None
    reclaim = (float(bar.close) > reclaim_line if _finite(bar.close) and _finite(reclaim_line) else None)
    prior_close = previous_bar.close if previous_bar else None
    nondeclining = (float(bar.close) >= float(prior_close)
                    if _finite(bar.close) and _finite(prior_close) else None)
    price_range = (float(bar.high)-float(bar.low)
                   if _finite(bar.high) and _finite(bar.low) else None)
    close_location = ((float(bar.close)-float(bar.low))/price_range
                      if _finite(bar.close) and price_range is not None and price_range > 0 else None)
    location_ok = close_location >= policy.minimum_close_location if _finite(close_location) else None
    rvol, volume_samples, volume_denominator = volume_ratio(bar, history)
    rvol_ok = rvol >= policy.minimum_rvol20 if _finite(rvol) else None
    rvol_reasons = ([] if rvol_ok is not None else
        ["RVOL_DENOMINATOR_NONPOSITIVE"] if volume_denominator is not None and volume_denominator <= 0 else
        [f"RVOL_PRIOR_SAMPLE_COUNT:{volume_samples}"])
    held = (support_fact is not None and support_fact.test_status == "HELD" and
            support_fact.first_held_at is not None and support_fact.first_held_at <= session and
            support_fact.broken_at is None)
    support_ok = held if support_fact is not None else None
    structure_ok, health_ok, phase_ok = _trend_predicates(bar)
    distance = ((float(bar.close)-zone_upper)/float(bar.atr14)
                if valid_atr and _finite(bar.close) else None)
    distance_ok = distance <= policy.maximum_entry_distance_atr if _finite(distance) else None
    upper_wick = ((float(bar.high)-max(float(bar.open), float(bar.close)))/float(bar.atr14)
                  if valid_atr and all(_finite(x) for x in (bar.open, bar.high, bar.close)) else None)
    rejection = (upper_wick >= policy.upper_wick_rejection_atr and
                 close_location <= policy.upper_rejection_close_location
                 if _finite(upper_wick) and _finite(close_location) else None)
    no_rejection = None if rejection is None else not rejection
    conditions = [
        _condition("CLOSE_ABOVE_FIXED_RECLAIM", session, "CONFIRMATION", bar.close, ">", reclaim_line,
                   "price", reclaim, refs, ["ATR_INVALID"] if reclaim is None and not frozen_atr_ok else []),
        _condition("CLOSE_NOT_BELOW_PRIOR_CLOSE", session, "CONFIRMATION", bar.close, ">=", prior_close,
                   "price", nondeclining, refs, ["PRIOR_CLOSE_MISSING"] if nondeclining is None else []),
        _condition("CLOSE_LOCATION", session, "CONFIRMATION", close_location, ">=",
                   policy.minimum_close_location, "ratio", location_ok, refs,
                   ["ZERO_OR_INVALID_DAILY_RANGE"] if location_ok is None else []),
        _condition("RVOL20", session, "CONFIRMATION", rvol, ">=", policy.minimum_rvol20,
                   "ratio", rvol_ok, refs+[f"volume_denominator:{volume_denominator}"],
                   rvol_reasons),
        _condition("SUPPORT_HELD", session, "CONFIRMATION", support_ok, "==", True,
                   "boolean", support_ok, refs,
                   ["SUPPORT_FACT_MISSING"] if support_fact is None else
                   ["SUPPORT_NOT_HELD"] if not support_ok else []),
        _condition("STRUCTURE_BULLISH_CONFIRMATION", session, "CONFIRMATION",
                   bar.structure_state, "==", "bullish", predicate=structure_ok,
                   refs=[f"structure:{session}"], reasons=["STRUCTURE_UNKNOWN"] if structure_ok is None else []),
        _condition("TREND_HEALTH_QUALIFIED_CONFIRMATION", session, "CONFIRMATION",
                   bar.trend_health, "IN", "strong|healthy", predicate=health_ok,
                   refs=[x for x in (f"trend_health:{session}", bar.trend_health_source) if x],
                   reasons=["TREND_HEALTH_UNKNOWN"] if health_ok is None else []),
        _condition("SHORT_TERM_PHASE_NOT_BLOCKED_CONFIRMATION", session, "CONFIRMATION",
                   bar.short_term_phase, "NOT_IN", "explicit_blocking_phases", predicate=phase_ok,
                   refs=[x for x in (f"short_term_phase:{session}", bar.short_term_phase_source) if x],
                   reasons=["SHORT_TERM_PHASE_UNKNOWN"] if phase_ok is None else
                           [f"SHORT_TERM_PHASE_BLOCKED:{bar.short_term_phase}"] if not phase_ok else []),
        _condition("DISTANCE_FROM_FIXED_ZONE", session, "CONFIRMATION", distance, "<=",
                   policy.maximum_entry_distance_atr, "ATR", distance_ok, refs,
                   ["CURRENT_ATR_INVALID"] if distance_ok is None else []),
        _condition("NO_LONG_UPPER_WICK_REJECTION", session, "CONFIRMATION", rejection, "==", False,
                   "boolean", no_rejection, refs,
                   ["UPPER_WICK_INPUT_UNKNOWN"] if no_rejection is None else
                   ["LONG_UPPER_WICK_CONFIRMATION_BLOCKER"] if not no_rejection else []),
        _condition("RSI_HIGH_DIAGNOSTIC", session, "DIAGNOSTIC", bar.rsi14, ">=", 70.0,
                   "index", (float(bar.rsi14) >= 70 if _finite(bar.rsi14) else None),
                   [f"rsi:{session}"], ["RSI_IS_DIAGNOSTIC_ONLY"]),
    ]
    return conditions


def current_eligibility_conditions(*, bar: OpportunityFeatureBar, zone_upper: float,
                                   support_fact: OpportunitySupportFact | None,
                                   policy: OpportunityPolicy):
    session = bar.session.isoformat()
    valid_atr = _finite(bar.atr14) and float(bar.atr14) > 0
    distance = ((float(bar.close)-zone_upper)/float(bar.atr14)
                if valid_atr and _finite(bar.close) else None)
    support_alive = None if support_fact is None else support_fact.broken_at is None
    structure_ok, health_ok, phase_ok = _trend_predicates(bar)
    return [
        _condition("SUPPORT_STILL_VALID", session, "CURRENT_ELIGIBILITY",
                   support_alive, "==", True, "boolean", support_alive,
                   [support_fact.support_result_id, support_fact.zone_id] if support_fact else [],
                   ["SUPPORT_FACT_MISSING"] if support_fact is None else
                   ["SUPPORT_BROKEN"] if not support_alive else []),
        _condition("STRUCTURE_STILL_BULLISH", session, "CURRENT_ELIGIBILITY",
                   bar.structure_state, "==", "bullish", predicate=structure_ok,
                   refs=[f"structure:{session}"], reasons=["STRUCTURE_UNKNOWN"] if structure_ok is None else []),
        _condition("TREND_HEALTH_STILL_QUALIFIED", session, "CURRENT_ELIGIBILITY",
                   bar.trend_health, "IN", "strong|healthy", predicate=health_ok,
                   refs=[x for x in (f"trend_health:{session}", bar.trend_health_source) if x],
                   reasons=["TREND_HEALTH_UNKNOWN"] if health_ok is None else []),
        _condition("SHORT_TERM_PHASE_STILL_VALID", session, "CURRENT_ELIGIBILITY",
                   bar.short_term_phase, "NOT_IN", "explicit_blocking_phases", predicate=phase_ok,
                   refs=[x for x in (f"short_term_phase:{session}", bar.short_term_phase_source) if x],
                   reasons=["SHORT_TERM_PHASE_UNKNOWN"] if phase_ok is None else
                           [f"SHORT_TERM_PHASE_BLOCKED:{bar.short_term_phase}"] if not phase_ok else []),
        _condition("CURRENT_DISTANCE_FROM_FIXED_ZONE", session, "CURRENT_ELIGIBILITY",
                   distance, "<=", policy.maximum_entry_distance_atr, "ATR",
                   distance <= policy.maximum_entry_distance_atr if _finite(distance) else None,
                   [f"bar:{session}"], ["CURRENT_ATR_INVALID"] if distance is None else []),
    ]
