"""Pure shallow-pullback facts; lifecycle and storage belong to shared callers."""
from __future__ import annotations

import hashlib
import json

from pcs.analysis_contracts import CapabilityStatus
from pcs.trend.selection_models import ShallowPullbackInput, ShallowPullbackState, SetupEvidence


def _id(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def detect_shallow_pullback(input: ShallowPullbackInput) -> SetupEvidence:
    from pcs.trend.setup_detectors import _condition, _finite, _trend_predicates

    ctx, view, policy = input.call_context, input.feature_view, input.effective_policy
    day = ctx.effective_daily_session
    if ctx.symbol != view.symbol or not day:
        raise ValueError("SHALLOW_INPUT_IDENTITY_MISMATCH")
    if view.input_kind == "VERIFIED_CANONICAL" and not (
            view.source.validated and view.source.sha256 and view.source.record_identity):
        raise ValueError("SHALLOW_UNVERIFIED_SOURCE")
    if view.input_kind == "TEST" and view.source.source_kind != "TEST":
        raise ValueError("SHALLOW_TEST_IDENTITY_REQUIRED")
    if not view.price_basis or not view.corporate_action_version or not view.indicator_identity:
        raise ValueError("SHALLOW_PRICE_INDICATOR_IDENTITY_MISSING")
    sessions = view.expected_sessions
    if sessions != sorted(set(sessions)) or day not in sessions:
        raise ValueError("SHALLOW_SESSION_COVERAGE_INVALID")
    bars = {b.session.isoformat(): b for b in view.bars if b.session.isoformat() <= day}
    if len(bars) != len([b for b in view.bars if b.session.isoformat() <= day]):
        raise ValueError("SHALLOW_DUPLICATE_SESSION")
    bar = bars.get(day)
    source_identity = _id([view.source.model_dump(mode="json"),
        view.indicator_identity, view.price_basis, view.corporate_action_version])
    policy_identity = _id(policy.model_dump(mode="json"))
    prior = input.prior_state
    if prior and (prior.symbol != ctx.symbol or prior.source_identity != source_identity or
            prior.policy_identity != policy_identity or prior.evaluated_through > day):
        raise ValueError("SHALLOW_PRIOR_IDENTITY_REPLAY_REQUIRED")
    def prefix_hash(end):
        return _id([bars[s].model_dump(mode="json") for s in sorted(bars) if s <= end])
    if prior and prior.input_prefix_sha256 != prefix_hash(prior.evaluated_through):
        raise ValueError("SHALLOW_PRIOR_PREFIX_REPLAY_REQUIRED")
    facts = [f for f in input.support_facts if f.session == day]
    if prior:
        legal = [f for f in facts if f.zone_id == prior.zone_id and f.test_id == prior.test_id]
    else:
        legal = [f for f in facts if f.touch_session == day and f.zone_available_at < day]
    legal.sort(key=lambda f: (abs(bar.close-(f.zone_lower+f.zone_upper)/2)
        if bar and _finite(bar.close) else float("inf"), f.zone_available_at, f.zone_id, f.test_id))
    support = legal[0] if legal else None
    conditions, missing = [], []
    def condition(name, left, op, right, value, unit=None, reasons=(), refs=(), role="DISCOVERY"):
        conditions.append(_condition(name, day, role, left, op, right, unit,
            value, refs, reasons))

    condition("LEGAL_SUPPORT_TOUCH", support.test_id if support else None, "IS_NOT", None,
        None if prior and support is None else support is not None,
        reasons=["SUPPORT_FACT_MISSING"] if prior and not support else
                ["NO_LEGAL_SHALLOW_TOUCH"] if not support else [],
        refs=[support.support_result_id, support.zone_id, support.test_id] if support else [])
    if bar:
        structure, health, phase = _trend_predicates(bar)
    else:
        structure = health = phase = None
    for name, value, passed, producer in (
        ("STRUCTURE_BULLISH", bar.structure_state if bar else None, structure, "pcs.trend.market_structure"),
        ("TREND_HEALTH_QUALIFIED", bar.trend_health if bar else None, health, bar.trend_health_source if bar else None),
        ("SHORT_TERM_PHASE_NOT_BLOCKED", bar.short_term_phase if bar else None, phase, bar.short_term_phase_source if bar else None)):
        condition(name, value, "SATISFIES", True, passed,
            reasons=[name+"_UNKNOWN"] if passed is None else [name+"_NOT_SATISFIED"] if not passed else [],
            refs=[p for p in (view.source.source_id, producer) if p])
    state = prior
    window, peak_candidates = [], []
    if support or prior:
        touch = prior.touch_session if prior else support.touch_session
        ti = sessions.index(touch) if touch in sessions else -1
        if not prior:
            window = sessions[max(0, ti-policy.peak_sessions):ti] if ti >= 0 else []
            missing = [s for s in window if s not in bars or not _finite(bars[s].high) or bars[s].high <= 0]
            complete = len(window) == policy.peak_sessions and not missing
            condition("PEAK_WINDOW_COMPLETE", len(window)-len(missing), "==", policy.peak_sessions,
                True if complete else None, "sessions", ["PEAK_WINDOW_INCOMPLETE"] if not complete else [])
            previous = sessions[ti-1] if ti > 0 else None
            atr = bars[previous].atr14 if previous in bars else None
            atr_ok = _finite(atr) and atr > 0
            condition("DEPTH_ANCHOR_ATR_VALID", atr, ">", 0, True if atr_ok else None,
                "USD", ["PREVIOUS_SESSION_ATR_MISSING_OR_INVALID"] if not atr_ok else [],
                refs=[f"{view.source.source_id}:{previous}:atr14"])
            if complete and atr_ok and bar and _finite(bar.low):
                peak_candidates = [{"session": s, "high": bars[s].high} for s in window]
                peak_session = max(window, key=lambda s: (bars[s].high, s))
                peak = bars[peak_session].high
                observed = [s for s in sessions if peak_session < s <= day]
                missing += [s for s in observed if s not in bars or not _finite(bars[s].low)]
                if not missing:
                    low_session = min(observed, key=lambda s: (bars[s].low, s))
                    low = bars[low_session].low
                    depth = (peak-low)/atr
                    first_exceeded = next((s for s in observed
                        if (peak-bars[s].low)/atr > policy.maximum_depth_atr), None)
                    state = ShallowPullbackState(symbol=ctx.symbol, zone_id=support.zone_id,
                        test_id=support.test_id, touch_session=touch, peak_price=peak,
                        peak_session=peak_session, peak_known_at=peak_session,
                        peak_window_start=window[0], peak_window_end=window[-1], peak_samples=len(window),
                        depth_anchor_atr=atr, depth_anchor_session=previous, touch_low=bar.low,
                        depth_at_touch=(peak-bar.low)/atr, episode_low=low, episode_low_session=low_session,
                        current_depth_atr=depth, first_depth_exceeded=first_exceeded,
                        evaluated_through=day, input_prefix_sha256=prefix_hash(day), source_identity=source_identity,
                        policy_identity=policy_identity, price_basis=view.price_basis,
                        corporate_action_version=view.corporate_action_version)
        else:
            window = sessions[max(0, ti-policy.peak_sessions):ti]
            peak_candidates = [{"session": s, "high": bars[s].high} for s in window]
            condition("PEAK_WINDOW_COMPLETE", prior.peak_samples, "==", policy.peak_sessions,
                True, "sessions")
            condition("DEPTH_ANCHOR_ATR_VALID", prior.depth_anchor_atr, ">", 0, True, "USD",
                refs=[f"{view.source.source_id}:{prior.depth_anchor_session}:atr14"])
            pending = [s for s in sessions if prior.evaluated_through < s <= day]
            missing = [s for s in pending if s not in bars or not _finite(bars[s].low)]
            if not missing:
                low, low_day, exceeded = prior.episode_low, prior.episode_low_session, prior.first_depth_exceeded
                for s in pending:
                    if bars[s].low < low:
                        low, low_day = bars[s].low, s
                    if exceeded is None and (prior.peak_price-low)/prior.depth_anchor_atr > policy.maximum_depth_atr:
                        exceeded = s
                state = prior.model_copy(update={"episode_low": low, "episode_low_session": low_day,
                    "current_depth_atr": (prior.peak_price-low)/prior.depth_anchor_atr,
                    "first_depth_exceeded": exceeded, "evaluated_through": day,
                    "input_prefix_sha256": prefix_hash(day)})
        condition("CUMULATIVE_LOW_COVERAGE", len(missing), "==", 0,
            True if state and not missing else None, "sessions",
            ["CUMULATIVE_LOW_HISTORY_INCOMPLETE"] if missing or state is None else [])
        depth = state.depth_at_touch if state else None
        condition("SHALLOW_TOUCH_DEPTH", depth, "IN_CLOSED_INTERVAL",
            f"{policy.minimum_depth_atr}..{policy.maximum_depth_atr}",
            policy.minimum_depth_atr <= depth <= policy.maximum_depth_atr if depth is not None else None,
            "ATR", ["SHALLOW_TOUCH_DEPTH_UNKNOWN"] if depth is None else [])
        cumulative = state.current_depth_atr if state and not missing else None
        qualified = (cumulative <= policy.maximum_depth_atr and state.first_depth_exceeded is None
                     if cumulative is not None else None)
        condition("SHALLOW_CUMULATIVE_DEPTH", cumulative, "<=", policy.maximum_depth_atr,
            qualified, "ATR", ["SETUP_DEPTH_EXCEEDED"] if qualified is False else
            ["SHALLOW_CUMULATIVE_DEPTH_UNKNOWN"] if qualified is None else [])
        condition("REAL_DOWNWARD_MOVE", cumulative, ">", 0,
            cumulative > 0 if cumulative is not None else None, "ATR")
        condition("SUPPORT_NOT_BROKEN", support.broken_at if support else None, "IS", None,
            support.broken_at is None if support else None,
            reasons=["SUPPORT_FACT_MISSING"] if not support else [])
    gates = [c.predicate_value for c in conditions]
    detected = False if False in gates else True if all(x is True for x in gates) else None
    reasons = list(dict.fromkeys(r for c in conditions if c.predicate_value is not True for r in c.reason_codes))
    identity = {"symbol": ctx.symbol, "day": day, "policy": policy_identity,
        "source": source_identity, "state": state.model_dump(mode="json") if state else None,
        "conditions": [c.model_dump(mode="json") for c in conditions],
        "support": support.model_dump(mode="json") if support else None,
        "bars": [bars[s].model_dump(mode="json") for s in sorted(bars)],
        "calculation_version": policy.calculation_version}
    measurements = {"price_unit": "USD", "atr_unit": "USD", "depth_unit": "ATR",
        "percentage_unit": "ratio", "peak_definition": "max(high[b-20:b-1]); latest tie",
        "peak_candidates": peak_candidates, "depth_formula": "(frozen_peak - low) / ATR[b-1]",
        "episode_low_definition": "min(low), peak_session < session <= as_of",
        "current_atr": bar.atr14 if bar else None,
        "zone_anchor_atr": support.anchor_atr if support else None,
        "known_at_semantics": "completed session; exact provider timestamp unknown unless supplied"}
    return SetupEvidence(call_context=ctx, session=day, detected=detected, family="SHALLOW_PULLBACK",
        recent_high=state.peak_price if state else None,
        recent_high_session=state.peak_session if state else None, pullback_pct=None,
        distance_sma20_atr=None, distance_sma50_atr=None,
        legacy_pullback_state=bar.legacy_pullback_state if bar else None,
        legacy_pullback_reasons=bar.legacy_pullback_reasons if bar else [],
        selected_support=support, alternative_supports=legal[1:], conditions=conditions,
        reason_codes=reasons, symbol=ctx.symbol, as_of=day,
        status=CapabilityStatus.PARTIAL if detected is None or missing else CapabilityStatus.COMPLETED,
        run_id=ctx.run_id, request_id=ctx.request_id, result_id=_id(identity),
        data_timestamp=view.source_timestamp, effective_policy=policy, next_state=state,
        touch_depth_pct=(state.peak_price-state.touch_low)/state.peak_price if state else None,
        cumulative_depth_pct=(state.peak_price-state.episode_low)/state.peak_price if state and not missing else None,
        pullback_to_current_close_pct=(state.peak_price-bar.close)/state.peak_price
            if state and bar and _finite(bar.close) else None,
        missing_sessions=sorted(set(missing)), measurements=measurements,
        same_bar_new_high_and_touch=bool(state and bar and day == state.touch_session and
            _finite(bar.high) and bar.high > state.peak_price),
        trend_evidence=bar.model_dump(mode="json") if bar else {},
        provenance=[view.source, *view.auxiliary_sources])
