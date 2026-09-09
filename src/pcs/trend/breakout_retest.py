"""Causal breakout/retest observation over a prepared daily feature view."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import pandas as pd
import exchange_calendars as xc

from pcs.analysis_contracts import CapabilityStatus
from pcs.trend.selection_models import (
    BreakoutEvent, BreakoutRetestDay, BreakoutRetestInput, BreakoutRetestResult,
    BreakoutRetestState, OpportunityEvidenceGap, OpportunityStateName,
    EntryOpportunity, OpportunityCoverage, OpportunityDay, OpportunityEpisode,
    OpportunityStateCheckpoint, OpportunitySupportFact, OpportunityTransition,
    SupportFeatureBar, SupportSourceAnchor,
)
from pcs.trend.setup_detectors import (
    _condition, confirmation_conditions, current_eligibility_conditions, volume_ratio,
)
from pcs.trend.support_zones import create_fixed_support_zone, update_fixed_support_zone
from pcs.trend.opportunity_state import _resolve_requested_session, _valid_price_bar
from pcs.trend.lifecycle import (pending_sessions, committed_days,
    required_conjunction, requested_applicability)


def _hash(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _finite(value):
    return value is not None and math.isfinite(float(value))


def _policy_identity(input):
    """Hash only policy values consumed by the breakout/retest lifecycle."""
    opportunity = input.opportunity_policy
    support = input.support_policy
    return _hash({
        "breakout": input.effective_policy.model_dump(mode="json"),
        "confirmation_and_entry": {
            "calculation_version": opportunity.calculation_version,
            "reclaim_buffer_atr": opportunity.reclaim_buffer_atr,
            "confirmation_sessions": opportunity.confirmation_sessions,
            "entry_window_sessions": opportunity.entry_window_sessions,
            "minimum_close_location": opportunity.minimum_close_location,
            "minimum_rvol20": opportunity.minimum_rvol20,
            "maximum_entry_distance_atr": opportunity.maximum_entry_distance_atr,
            "upper_wick_rejection_atr": opportunity.upper_wick_rejection_atr,
            "upper_rejection_close_location": opportunity.upper_rejection_close_location,
            "upper_wick_role": opportunity.upper_wick_role,
        },
        "fixed_support": {
            "calculation_version": support.calculation_version,
            "zone_width_atr": support.zone_width_atr,
            "break_buffer_atr": support.break_buffer_atr,
            "held_rebound_atr": support.held_rebound_atr,
            "retest_departure_atr": support.retest_departure_atr,
            "confirmation_sessions": support.confirmation_sessions,
        },
    })


def _support_bar(bar):
    return SupportFeatureBar(session=bar.session, open=bar.open, high=bar.high,
        low=bar.low, close=bar.close, sma20=bar.sma20, sma50=bar.sma50, atr14=bar.atr14)


def _fact(event, session):
    if not event.retest_session or not event.zone.tests:
        return None
    test = event.zone.tests[0]
    return OpportunitySupportFact(session=session, support_result_id=event.breakout_id,
        zone_id=event.zone.zone_id, test_id=test.test_id, zone_lower=event.zone.lower,
        zone_upper=event.zone.upper, anchor_atr=event.zone.anchor_atr,
        invalidation_line=event.zone.invalidation_line,
        zone_available_at=event.zone.available_at, touch_session=test.touch_session,
        test_status=test.status, first_held_at=test.first_held_at,
        broken_at=event.zone.broken_at, zone_state=event.zone.state,
        source_ids=event.zone.observed_source_ids,
        sources=event.zone.observed_sources or event.zone.creation_sources,
        reason_codes=test.reason_codes)


def _snapshot(event):
    """Copy only facts already known at this point in the advancing lifecycle."""
    if event is None:
        return {}
    return dict(breakout_id=event.breakout_id, breakout_session=event.breakout_session,
        support_zone_id=event.zone.zone_id, retest_deadline=event.retest_deadline,
        retest_session=event.retest_session, confirmation_session=event.confirmation_session,
        confirmation_deadline=event.confirmation_deadline,
        entry_start=event.entry_start, entry_end=event.entry_end,
        support_test_id=event.zone.tests[0].test_id if event.retest_session and event.zone.tests else None)


def _stage(event):
    if event is None:
        return "NO_BREAKOUT"
    return {OpportunityStateName.WATCH: "WAITING_RETEST",
        OpportunityStateName.CONFIRMING: "WAITING_CONFIRMATION",
        OpportunityStateName.ENTRY_READY: "VERIFIED"}.get(event.state, event.state.value)


def _discovery(bar, history, sessions, bars, policy, view, support_policy):
    day = bar.session.isoformat()
    i = sessions.index(day)
    window = sessions[max(0, i-policy.resistance_sessions):i]
    missing = [s for s in window if s not in bars or not _finite(bars[s].high)]
    conditions = []
    complete = len(window) == policy.resistance_sessions and not missing
    conditions.append(_condition("BREAKOUT_RESISTANCE_WINDOW_COMPLETE", day, "DISCOVERY",
        len(window)-len(missing), "==", policy.resistance_sessions,
        predicate=True if complete else None, unit="sessions",
        reasons=[] if complete else ["BREAKOUT_RESISTANCE_WINDOW_INCOMPLETE"]))
    resistance = resistance_session = None
    candidates = []
    if complete:
        candidates = [{"session": s, "high": float(bars[s].high)} for s in window]
        resistance_session = max(window, key=lambda s: (float(bars[s].high), s))
        resistance = float(bars[resistance_session].high)
    atr_ok = _finite(bar.atr14) and float(bar.atr14) > 0
    conditions.append(_condition("BREAKOUT_ATR_VALID", day, "DISCOVERY", bar.atr14,
        ">", 0, predicate=True if atr_ok else None, unit="USD",
        reasons=[] if atr_ok else ["BREAKOUT_ATR_MISSING_OR_INVALID"],
        refs=[f"{view.source.source_id}:{day}:atr14"]))
    line = resistance + policy.breakout_buffer_atr*float(bar.atr14) if complete and atr_ok else None
    price_pass = float(bar.close) > line if _finite(bar.close) and _finite(line) else None
    conditions.append(_condition("BREAKOUT_CLOSE_STRICTLY_ABOVE_LINE", day, "DISCOVERY",
        bar.close, ">", line, predicate=price_pass, unit="USD",
        reasons=[] if price_pass is not None else ["BREAKOUT_PRICE_INPUT_UNKNOWN"],
        refs=[f"bar:{day}:close", f"resistance:{resistance_session}"]))
    rvol, samples, denominator = volume_ratio(bar, history)
    rvol_pass = rvol >= policy.minimum_breakout_rvol20 if _finite(rvol) else None
    conditions.append(_condition("BREAKOUT_RVOL20", day, "DISCOVERY", rvol, ">=",
        policy.minimum_breakout_rvol20, predicate=rvol_pass, unit="ratio",
        reasons=[] if rvol_pass is not None else [f"BREAKOUT_RVOL_PRIOR_SAMPLE_COUNT:{samples}"],
        refs=[f"bar:{day}:volume", "previous_20_completed_sessions:volume"]))
    structure = bar.structure_state == "bullish" if bar.structure_state is not None else None
    conditions.append(_condition("BREAKOUT_STRUCTURE_BULLISH", day, "DISCOVERY",
        bar.structure_state, "==", "bullish", predicate=structure,
        reasons=[] if structure is not None else ["BREAKOUT_STRUCTURE_UNKNOWN"],
        refs=[f"structure:{day}"]))
    for cid, left, producer in (("BREAKOUT_TREND_HEALTH_DIAGNOSTIC", bar.trend_health,
            bar.trend_health_source), ("BREAKOUT_PHASE_DIAGNOSTIC", bar.short_term_phase,
            bar.short_term_phase_source)):
        conditions.append(_condition(cid, day, "DIAGNOSTIC", left, "RECORDED", True,
            predicate=None if left is None else True,
            refs=[x for x in (producer, f"bar:{day}") if x],
            reasons=[cid+"_UNKNOWN"] if left is None else []))
    detected = required_conjunction(conditions)
    return detected, conditions, missing, resistance, resistance_session, candidates, rvol, denominator, line


def detect_breakout_retest(input: BreakoutRetestInput) -> BreakoutRetestResult:
    """Detect and advance breakout/retest facts; never reads or writes storage."""
    ctx, view, policy = input.call_context, input.feature_view, input.effective_policy
    requested_session, request_semantics = _resolve_requested_session(ctx, input.calendar)
    day = ctx.effective_daily_session
    if view.symbol != ctx.symbol or not day:
        raise ValueError("BREAKOUT_INPUT_IDENTITY_MISMATCH")
    if view.input_kind == "VERIFIED_CANONICAL" and not (
            view.source.validated and view.source.sha256 and view.source.record_identity):
        raise ValueError("BREAKOUT_UNVERIFIED_SOURCE")
    if view.input_kind == "TEST" and view.source.source_kind != "TEST":
        raise ValueError("BREAKOUT_TEST_IDENTITY_REQUIRED")
    sessions = list(view.expected_sessions)
    if sessions != sorted(set(sessions)) or day not in sessions:
        raise ValueError("BREAKOUT_SESSION_COVERAGE_INVALID")
    day_i = sessions.index(day)
    if len(sessions)-day_i <= policy.retest_wait_sessions+input.opportunity_policy.confirmation_sessions+input.opportunity_policy.entry_window_sessions:
        calendar = xc.get_calendar(input.calendar)
        tail = [str(s.date()) for s in calendar.sessions_window(pd.Timestamp(sessions[-1]), 30)][1:]
        sessions += [s for s in tail if s not in sessions]
    bars = {b.session.isoformat(): b for b in view.bars if b.session.isoformat() <= day}
    if len(bars) != len([b for b in view.bars if b.session.isoformat() <= day]):
        raise ValueError("BREAKOUT_DUPLICATE_SESSION")
    source_identity = _hash([view.source.model_dump(mode="json"), view.indicator_identity,
        view.price_basis, view.corporate_action_version])
    policy_identity = _policy_identity(input)
    prefix_hash = lambda through: _hash([bars[s].model_dump(mode="json")
        for s in sorted(bars) if s <= through])
    prior = input.prior_state
    if prior and (prior.symbol != ctx.symbol or prior.source_identity != source_identity or
            prior.policy_identity != policy_identity or prior.indicator_identity != view.indicator_identity or
            prior.price_basis != view.price_basis or
            prior.corporate_action_version != view.corporate_action_version or
            prior.calculation_version != policy.calculation_version or
            (prior.evaluated_through is not None and (prior.evaluated_through > day or
            prior.input_prefix_sha256 != prefix_hash(prior.evaluated_through)))):
        raise ValueError("BREAKOUT_PRIOR_REPLAY_REQUIRED")
    if prior and ([d.session for d in committed_days(prior.timeline, prior.evaluated_through)] !=
            [s for s in sessions if prior.evaluated_through and prior.analysis_start <= s <= prior.evaluated_through]):
        raise ValueError("BREAKOUT_PRIOR_TIMELINE_INCOMPLETE")
    events = deepcopy(prior.events) if prior else []
    timeline = deepcopy(committed_days(prior.timeline, prior.evaluated_through)) if prior else []
    start_after = prior.evaluated_through if prior else None
    analysis_start = prior.analysis_start if prior else view.analysis_start
    process = pending_sessions(sessions, analysis_start, day, start_after)
    evaluated = start_after
    committed_count = 0
    missing_sessions = []
    coverage_gaps = []
    support_history = []
    parent = events[-1] if events else None
    active = next((e for e in reversed(events) if e.state not in {
        OpportunityStateName.EXPIRED, OpportunityStateName.INVALIDATED}), None)
    for session in process:
        bar = bars.get(session)
        if bar is None or not _valid_price_bar(bar):
            missing_sessions = [s for s in process if s >= session and
                (s not in bars or not _valid_price_bar(bars[s]))]
            why = "DAILY_BAR_MISSING" if bar is None else "DAILY_OHLC_INVALID"
            known = active or parent
            timeline.append(BreakoutRetestDay(session=session,
                **_snapshot(known), state=known.state if known else OpportunityStateName.NO_SETUP,
                stage=_stage(known),
                eligible=None, reason_codes=[why, "PROCESSING_STOPPED_AT_FIRST_GAP"]))
            for gap_session in missing_sessions:
                gap_reason = "DAILY_BAR_MISSING" if gap_session not in bars else "DAILY_OHLC_INVALID"
                coverage_gaps.append(OpportunityEvidenceGap(session=gap_session,
                    condition_id=gap_reason, role="INPUT", evidence_refs=[view.source.source_id, f"bar:{gap_session}"],
                    reason_codes=[gap_reason], affected_outputs=["current_assessment", "coverage"]))
            break
        i = sessions.index(session)
        conditions, reasons = [], []
        detected = False
        if active and active.state in {OpportunityStateName.EXPIRED, OpportunityStateName.INVALIDATED}:
            parent, active = active, None
        if active:
            old_test_count = len(active.zone.tests)
            zone, changes = update_fixed_support_zone(active.zone, _support_bar(bar), sessions,
                                                      input.support_policy, support_history)
            active = active.model_copy(update={"zone": zone})
            events[-1] = active
            new_test = len(zone.tests) > old_test_count
            if new_test and active.retest_session is None and session <= active.retest_deadline:
                test = zone.tests[0]
                active = active.model_copy(update={"retest_session": test.touch_session,
                    "confirmation_deadline": test.confirmation_deadline,
                    "state": OpportunityStateName.CONFIRMING,
                    "reason_codes": active.reason_codes+["FIRST_RETEST_RECORDED"]})
                events[-1] = active
            close_break = float(bar.close) < zone.invalidation_line
            structure_break = bar.structure_state == "bearish"
            conditions.extend([
                _condition("BREAKOUT_FIXED_LINE_NOT_BROKEN", session, "INVALIDATION", bar.close,
                    ">=", zone.invalidation_line, predicate=not close_break, unit="USD",
                    refs=[zone.zone_id, f"bar:{session}:close"]),
                _condition("BREAKOUT_STRUCTURE_NOT_BEARISH", session, "INVALIDATION",
                    bar.structure_state, "!=", "bearish",
                    predicate=None if bar.structure_state is None else not structure_break,
                    reasons=["BREAKOUT_STRUCTURE_UNKNOWN"] if bar.structure_state is None else [],
                    refs=[f"structure:{session}"])])
            if close_break or structure_break:
                reasons += ["FIXED_SUPPORT_INVALIDATED" if close_break else "STRUCTURE_INVALIDATED"]
                active = active.model_copy(update={"state": OpportunityStateName.INVALIDATED,
                    "terminal_session": session, "reason_codes": active.reason_codes+reasons})
                events[-1] = active
            elif active.retest_session is None:
                if session > active.retest_deadline:
                    reasons.append("FIRST_RETEST_WINDOW_EXPIRED")
                    active = active.model_copy(update={"state": OpportunityStateName.EXPIRED,
                        "terminal_session": session, "reason_codes": active.reason_codes+reasons})
                    events[-1] = active
            elif active.confirmation_session is None:
                if session > active.confirmation_deadline:
                    reasons.append("RETEST_CONFIRMATION_WINDOW_EXPIRED")
                    active = active.model_copy(update={"state": OpportunityStateName.EXPIRED,
                        "terminal_session": session, "reason_codes": active.reason_codes+reasons})
                    events[-1] = active
                elif session > active.retest_session:
                    previous = bars.get(sessions[i-1]) if i > 0 else None
                    fact = _fact(active, session)
                    confirm = confirmation_conditions(bar=bar, previous_bar=previous,
                        history=list(bars.values()), zone_upper=zone.upper,
                        anchor_atr=zone.anchor_atr, zone_id=zone.zone_id,
                        test_id=fact.test_id if fact else "UNAVAILABLE", support_fact=fact,
                        policy=input.opportunity_policy)
                    conditions.extend(confirm)
                    if required_conjunction(conditions) is True:
                        entry_start = sessions[i+1]
                        entry_end = sessions[i+input.opportunity_policy.entry_window_sessions]
                        active = active.model_copy(update={"confirmation_session": session,
                            "entry_start": entry_start, "entry_end": entry_end,
                            "state": OpportunityStateName.ENTRY_READY,
                            "reason_codes": active.reason_codes+["BREAKOUT_RETEST_VERIFIED"]})
                        events[-1] = active
            else:
                if session > active.entry_end:
                    active = active.model_copy(update={"state": OpportunityStateName.EXPIRED,
                        "terminal_session": session,
                        "reason_codes": active.reason_codes+["ENTRY_WINDOW_EXPIRED"]})
                    events[-1] = active
                else:
                    current = current_eligibility_conditions(bar=bar, zone_upper=zone.upper,
                        support_fact=_fact(active, session),
                        policy=input.opportunity_policy)
                    conditions.extend(current)
            if active.state in {OpportunityStateName.EXPIRED, OpportunityStateName.INVALIDATED}:
                parent = active
        terminal = parent if parent and parent.state in {
            OpportunityStateName.EXPIRED, OpportunityStateName.INVALIDATED} else None
        if terminal and terminal.restart_armed_at is None and session > terminal.terminal_session and float(bar.close) <= terminal.resistance:
            terminal = terminal.model_copy(update={"restart_armed_at": session,
                "reason_codes": terminal.reason_codes+["RESTART_ARMED_AFTER_CLOSE_AT_OR_BELOW_OLD_RESISTANCE"]})
            events[-1] = terminal
            parent, active = terminal, None
        may_discover = (not events or (active is None and parent is not None and
            parent.restart_armed_at is not None and session > parent.restart_armed_at))
        if may_discover:
            detected, discovery, missing, resistance, resistance_session, candidates, rvol, denom, line = _discovery(
                bar, [bars[s] for s in sorted(bars) if s < session], sessions, bars,
                policy, view, input.support_policy)
            conditions.extend(discovery)
            if detected is True:
                source_id = _hash([ctx.symbol, session, resistance, resistance_session,
                                   policy.calculation_version])
                anchor = SupportSourceAnchor(source_id=source_id,
                    source_type="BREAKOUT_RESISTANCE", price=resistance,
                    observed_at=session, available_at=session)
                zone = create_fixed_support_zone(ctx.symbol, anchor, float(bar.atr14),
                                                 input.support_policy, view)
                breakout_id = _hash([ctx.symbol, session, resistance, resistance_session,
                    float(bar.atr14), policy_identity, source_identity,
                    view.price_basis, view.corporate_action_version])
                event = BreakoutEvent(breakout_id=breakout_id,
                    parent_breakout_id=parent.breakout_id if parent else None,
                    breakout_session=session, resistance=resistance,
                    resistance_session=resistance_session, resistance_known_at=session,
                    resistance_window_start=candidates[0]["session"],
                    resistance_window_end=candidates[-1]["session"],
                    resistance_samples=len(candidates), resistance_candidates=candidates,
                    breakout_atr=float(bar.atr14), breakout_line=line,
                    breakout_close=float(bar.close),
                    breakout_distance_atr=(float(bar.close)-resistance)/float(bar.atr14),
                    breakout_rvol20=float(rvol), breakout_volume=float(bar.volume),
                    breakout_volume_denominator=float(denom), zone=zone,
                    retest_deadline=sessions[i+policy.retest_wait_sessions],
                    reason_codes=["BREAKOUT_CONFIRMED_WAITING_FIRST_RETEST",
                                  "BREAKOUT_DAY_NOT_A_RETEST", "INTRADAY_ORDER_UNKNOWN"])
                events.append(event); active = event; parent = event
        current = active if active and active.state not in {
            OpportunityStateName.EXPIRED, OpportunityStateName.INVALIDATED} else parent
        eligible = bool(current and current.state == OpportunityStateName.ENTRY_READY and
                    current.entry_start <= session <= current.entry_end and
                    all(c.predicate_value is True for c in conditions if c.role == "CURRENT_ELIGIBILITY"))
        if may_discover and detected is None:
            eligible = None
        elif current and current.state not in {OpportunityStateName.EXPIRED, OpportunityStateName.INVALIDATED}:
            gates = required_conjunction(conditions)
            if gates is None:
                eligible = None
            elif gates is False:
                eligible = False
        timeline.append(BreakoutRetestDay(session=session,
            **_snapshot(current), state=current.state if current else OpportunityStateName.NO_SETUP,
            stage=_stage(current), eligible=eligible, conditions=conditions,
            reason_codes=reasons or (["BREAKOUT_RETEST_VERIFIED"] if eligible else [])))
        evaluated = session
        committed_count += 1
    current = events[-1] if events else None
    current_day = timeline[-1] if timeline else None
    for item in timeline:
        for c in item.conditions:
            if c.role != "DIAGNOSTIC" and c.predicate_value is None:
                coverage_gaps.append(OpportunityEvidenceGap(session=item.session,
                    condition_id=c.condition_id, role=c.role,
                    reason_codes=c.reason_codes or ["CONDITION_INPUT_UNKNOWN"],
                    evidence_refs=c.source_refs or [view.source.source_id, f"bar:{item.session}"],
                    affected_outputs=["current_assessment", "coverage"] if item == current_day else ["coverage"]))
    current_gaps = [g for g in coverage_gaps if "current_assessment" in g.affected_outputs]
    requested_eligible = requested_applicability(requested=requested_session,
        evidence=day, semantics=request_semantics,
        eligible=current_day.eligible if current_day else None,
        entry_end=current.entry_end if current else None)
    if request_semantics == "CURRENT_EOD" and requested_session != day and requested_eligible is None:
        cal = xc.get_calendar(input.calendar)
        needed = [str(s.date()) for s in cal.sessions_in_range(min(day, requested_session), max(day, requested_session))
                  if str(s.date()) != day]
        for s in needed:
            current_gaps.append(OpportunityEvidenceGap(session=s,
                condition_id="REQUESTED_SESSION_EVIDENCE_MISSING", role="INPUT",
                reason_codes=["REQUESTED_SESSION_EVIDENCE_MISSING"],
                evidence_refs=[view.source.source_id, f"bar:{s}"], affected_outputs=["current_assessment"]))
    status = CapabilityStatus.PARTIAL if requested_eligible is None or missing_sessions else CapabilityStatus.COMPLETED
    state = BreakoutRetestState(symbol=ctx.symbol, events=events, timeline=timeline,
        analysis_start=analysis_start,
        evaluated_through=evaluated, input_prefix_sha256=prefix_hash(evaluated) if evaluated else _hash([]),
        coverage_missing_evidence=coverage_gaps,
        source_identity=source_identity, policy_identity=policy_identity,
        indicator_identity=view.indicator_identity, price_basis=view.price_basis,
        corporate_action_version=view.corporate_action_version,
        state_revision=(prior.state_revision if prior else 0)+committed_count)
    identity = [ctx.symbol, day, source_identity, policy_identity,
        [e.model_dump(mode="json") for e in events], [d.model_dump(mode="json") for d in timeline],
        evaluated, missing_sessions, policy.calculation_version, requested_session, request_semantics]
    reasons = list(dict.fromkeys([r for d in timeline for r in d.reason_codes] +
        (["DAILY_SESSION_GAPS"] if missing_sessions else []) +
        [r for g in current_gaps for r in g.reason_codes]))
    return BreakoutRetestResult(symbol=ctx.symbol, as_of=day, status=status,
        data_timestamp=view.source_timestamp, run_id=ctx.run_id, request_id=ctx.request_id,
        result_id=_hash(identity), reason_codes=reasons, call_context=ctx,
        effective_policy=policy, policy_sha256=policy_identity, events=events,
        timeline=timeline, current_event_id=current.breakout_id if current else None,
        eligible_at_requested_time=requested_eligible,
        requested_session=requested_session, request_time_semantics=request_semantics,
        current_missing_details=current_gaps,
        evaluated_through=evaluated, missing_sessions=missing_sessions,
        coverage_missing_evidence=coverage_gaps, next_state=state,
        provenance=[view.source, *view.auxiliary_sources],
        explanation=(f"评估至{evaluated}；突破事件{len(events)}个；当前状态"
            f"{current_day.state.value if current_day else '未知'}；请求交易日{requested_session}，"
            f"请求时资格{requested_eligible if requested_eligible is not None else '未知'}。观察结果不是下单授权。"))


def evaluate_breakout_opportunity(input):
    """Adapt breakout facts to the shared opportunity envelope/state vocabulary."""
    previous = next((r for r in input.prior_family_results if r.family == "BREAKOUT_RETEST"), None)
    prior = previous.breakout_result.next_state if previous and previous.breakout_result else None
    result = detect_breakout_retest(BreakoutRetestInput(call_context=input.call_context,
        feature_view=input.feature_view, effective_policy=input.breakout_policy,
        opportunity_policy=input.effective_policy,
        support_policy=input.support_policy,
        prior_state=prior, calendar=input.calendar))
    episodes = []
    for event in result.events:
        if not event.retest_session:
            continue
        test = event.zone.tests[0]
        economic = _hash([input.call_context.symbol, event.retest_session])
        opportunity = _hash([economic, event.breakout_id, event.zone.zone_id, test.test_id,
                             result.policy_sha256, "entry-opportunity-v2.4"])
        episodes.append(OpportunityEpisode(economic_episode_id=economic,
            opportunity_id=opportunity, family="BREAKOUT_RETEST",
            setup_date=event.breakout_session, touch_date=event.retest_session,
            confirmation_deadline=event.confirmation_deadline,
            confirmation_date=event.confirmation_session, entry_start=event.entry_start,
            entry_end=event.entry_end, terminal_date=event.terminal_session,
            state=event.state, zone_id=event.zone.zone_id, test_id=test.test_id,
            zone_lower=event.zone.lower, zone_upper=event.zone.upper,
            anchor_atr=event.zone.anchor_atr,
            invalidation_line=event.zone.invalidation_line,
            zone_available_at=event.zone.available_at,
            recent_high=event.resistance, recent_high_session=event.resistance_session,
            parent_episode_id=event.parent_breakout_id,
            reason_codes=event.reason_codes))
    by_breakout = {e.breakout_id: e for e in result.events}
    by_opportunity = {e.setup_date: e for e in episodes}
    timeline = []
    transitions = []
    old = None
    for item in result.timeline:
        episode = by_opportunity.get(item.breakout_session) if item.retest_session else None
        day = OpportunityDay(session=item.session, state=item.state,
            capability_status=CapabilityStatus.PARTIAL if item.eligible is None else CapabilityStatus.COMPLETED,
            economic_episode_id=episode.economic_episode_id if episode else None,
            opportunity_id=episode.opportunity_id if episode else None,
            setup_date=item.breakout_session,
            touch_date=item.retest_session, confirmation_deadline=item.confirmation_deadline,
            confirmation_date=item.confirmation_session,
            entry_start=item.entry_start, entry_end=item.entry_end,
            eligible=item.eligible, support_zone_id=item.support_zone_id,
            support_test_id=item.support_test_id, conditions=item.conditions,
            reason_codes=item.reason_codes)
        timeline.append(day)
        if old != item.state:
            transitions.append(OpportunityTransition(transition_id=_hash([
                item.breakout_id, item.session, old, item.state]), session=item.session,
                economic_episode_id=episode.economic_episode_id if episode else None,
                from_state=old, to_state=item.state, event_type="BREAKOUT_RETEST_STATE_CHANGED",
                reason_codes=item.reason_codes, evidence_refs=[x for x in (
                    item.breakout_id, item.support_test_id) if x]))
            old = item.state
    view = input.feature_view
    source_identity = result.next_state.source_identity
    support_identity = _hash([e.zone.model_dump(mode="json") for e in result.events])
    policy_hash = result.policy_sha256
    checkpoint = OpportunityStateCheckpoint(symbol=input.call_context.symbol,
        episodes=episodes, evaluated_through=result.evaluated_through,
        state_revision=result.next_state.state_revision, committed_result_id=None,
        input_prefix_sha256=result.next_state.input_prefix_sha256,
        source_identity=source_identity, support_identity=support_identity,
        policy_sha256=policy_hash, indicator_identity=view.indicator_identity,
        price_basis=view.price_basis, corporate_action_version=view.corporate_action_version,
        analysis_start=view.analysis_start)
    actual = [s for s in view.expected_sessions if view.analysis_start <= s <= input.call_context.effective_daily_session]
    coverage = OpportunityCoverage(expected_sessions=actual,
        actual_sessions=[s for s in actual if result.evaluated_through and s <= result.evaluated_through],
        missing_sessions=result.missing_sessions, analysis_start=view.analysis_start,
        evaluated_through=result.evaluated_through,
        indicator_seed_start=view.indicator_seed_start,
        indicator_identity=view.indicator_identity,
        legal_input_sha256=result.next_state.input_prefix_sha256,
        source_identity=source_identity, support_identity=support_identity,
        fields={"OHLC": CapabilityStatus.PARTIAL if result.missing_sessions else CapabilityStatus.COMPLETED,
            "VOLUME": CapabilityStatus.PARTIAL if any(b.volume is None for b in view.bars) else CapabilityStatus.COMPLETED,
            "BREAKOUT_SUPPORT": CapabilityStatus.COMPLETED},
        reason_codes=["DAILY_SESSION_GAPS"] if result.missing_sessions else [],
        display_sessions=actual, processed_sessions=[d.session for d in timeline])
    last = timeline[-1] if timeline else None
    current_event = by_breakout.get(result.current_event_id)
    current_episode = next((e for e in reversed(episodes)
        if current_event and e.setup_date == current_event.breakout_session), None)
    current_conditions = last.conditions if last else []
    current_gaps = result.current_missing_details
    entry_id = _hash(["entry-opportunity-v2.3-breakout-v2", result.result_id,
                      [e.model_dump(mode="json") for e in episodes]])
    checkpoint = checkpoint.model_copy(update={"committed_result_id": entry_id})
    return EntryOpportunity(symbol=input.call_context.symbol,
        version="1.3", calculation_version="entry-opportunity-v2.4",
        as_of=input.call_context.effective_daily_session, family="BREAKOUT_RETEST",
        status=result.status, state=last.state if last else None,
        last_known_state=last.state if last else None,
        evaluated_through=result.evaluated_through, last_known_session=result.evaluated_through,
        entry_permitted_from=current_event.entry_start if current_event else None,
        entry_permitted_until=current_event.entry_end if current_event else None,
        confirmation_deadline_elapsed_at_requested_session=(
            result.requested_session > current_event.confirmation_deadline
            if current_event and current_event.confirmation_deadline and not current_event.confirmation_session else
            False if current_event else None),
        entry_window_elapsed_at_requested_session=(
            result.requested_session > current_event.entry_end
            if current_event and current_event.entry_end else False if current_event else None),
        eligible_at_requested_time=result.eligible_at_requested_time,
        requested_session=result.requested_session,
        request_time_semantics=result.request_time_semantics,
        economic_episode_id=current_episode.economic_episode_id if current_episode else None,
        opportunity_id=current_episode.opportunity_id if current_episode else None,
        result_id=entry_id, matched_families=["BREAKOUT_RETEST"] if result.events else [],
        upstream_result_ids=[e.breakout_id for e in result.events],
        run_id=input.call_context.run_id, request_id=input.call_context.request_id,
        received_at=view.received_at,
        effective_policy=input.effective_policy.model_copy(update={"family": "BREAKOUT_RETEST",
            "policy_id": "breakout-retest-opportunity-v1"}),
        policy_sha256=policy_hash, call_context=input.call_context,
        episodes=episodes, timeline=timeline, transitions=transitions, detections=[],
        current_conditions=current_conditions,
        supporting_evidence=[c.condition_id for c in current_conditions if c.predicate_value is True],
        opposing_evidence=[c.condition_id for c in current_conditions if c.predicate_value is False],
        missing_evidence=list(dict.fromkeys([c.condition_id for c in current_conditions
            if c.predicate_value is None and c.role != "DIAGNOSTIC"]+[g.condition_id for g in current_gaps])),
        current_missing_details=current_gaps,
        coverage_missing_evidence=result.coverage_missing_evidence,
        next_observation_conditions=(["等待首次回踩，最晚至ret​est_deadline"]
            if current_event and not current_event.retest_session else
            ["等待回踩后首次确认，最晚至confirmation_deadline"]
            if current_event and not current_event.confirmation_session else
            ["在固定c+1至c+3窗口复核当前资格"]
            if current_event and current_event.entry_end else ["等待新的合法突破"]),
        legacy_opinion=input.legacy_opinion,
        opinion_differences=[{"field": "breakout_retest", "legacy": "NOT_RECORDED",
            "v2": last.state.value if last else None,
            "reason_code": "BREAKOUT_RETEST_OBSERVATION_ONLY"}],
        coverage=coverage, next_state=checkpoint,
        call_diagnostics=["BREAKOUT_PRIOR_STATE_COMPATIBLE_INCREMENTAL"] if prior else [],
        provenance=result.provenance, explanation=result.explanation,
        active_families=["BREAKOUT_RETEST"] if result.eligible_at_requested_time else [],
        breakout_result=result)
