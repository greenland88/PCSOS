"""Causal state machine for versioned entry-opportunity observations."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

import pandas as pd
import exchange_calendars as xc
from pcs.trend.lifecycle import pending_sessions, committed_days, requested_applicability

from pcs.analysis_contracts import CapabilityStatus
from pcs.trend.selection_models import (
    EntryOpportunity, OpportunityCoverage, OpportunityDay, OpportunityEpisode,
    OpportunityInput, OpportunityStateCheckpoint, OpportunityStateName,
    OpportunityTransition, OpportunityEvidenceGap, OpportunityDetection, ShallowPullbackInput,
)
from pcs.trend.setup_detectors import (
    confirmation_conditions, current_eligibility_conditions,
    detect_healthy_pullback,
    detect_shallow_pullback,
)

_IMPLEMENTATION_ID = "entry-opportunity-v2.2-step5-shallow-v1"


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False,
                                     default=str).encode()).hexdigest()


def _source_identity(view) -> str:
    return "sha256:" + _hash([view.source.source_kind, view.source.sha256,
        view.source.record_identity,
        [[s.source_kind, s.sha256, s.record_identity] for s in view.auxiliary_sources],
        view.price_basis, view.corporate_action_version])


def _policy_identity(policy, shallow_policy=None) -> str:
    payload = policy.model_dump(mode="json")
    if policy.family == "SHALLOW_PULLBACK":
        for key in ("healthy_pullback_min_pct", "healthy_pullback_max_pct", "shallow_pullback_max_pct",
                    "sma20_near_atr", "sma50_near_atr", "recent_high_sessions"):
            payload.pop(key, None)
        payload["shallow_policy"] = shallow_policy.model_dump(mode="json")
    return "sha256:" + _hash(payload)


def _resolve_requested_session(ctx, calendar):
    mode = str(ctx.mode).upper()
    try:
        requested = pd.Timestamp(ctx.requested_as_of)
    except (TypeError, ValueError):
        raise ValueError("OPPORTUNITY_REQUEST_TIME_INVALID") from None
    if mode == "HISTORICAL":
        if pd.isna(requested):
            raise ValueError("OPPORTUNITY_REQUEST_TIME_INVALID")
        return ctx.effective_daily_session, "HISTORICAL"
    if mode != "CURRENT_EOD":
        raise ValueError(f"OPPORTUNITY_REQUEST_MODE_UNSUPPORTED:{ctx.mode}")
    if pd.isna(requested) or requested.tzinfo is None:
        raise ValueError("OPPORTUNITY_CURRENT_EOD_REQUIRES_TIMEZONE_AWARE_TIMESTAMP")
    cal = xc.get_calendar(calendar)
    utc = requested.tz_convert("UTC")
    local_date = requested.tz_convert(str(cal.tz)).date()
    label = cal.date_to_session(pd.Timestamp(local_date), direction="previous")
    if utc < cal.session_close(label):
        label = cal.previous_session(label)
    return str(label.date()), "CURRENT_EOD"


def _valid_price_bar(bar) -> bool:
    values = (bar.open, bar.high, bar.low, bar.close)
    return all(v is not None and math.isfinite(float(v)) for v in values) and bar.high >= bar.low


def _transition(transitions, session, episode, old, new, event, reasons=(), refs=()):
    item = OpportunityTransition(transition_id="sha256:"+_hash([
        session, episode.economic_episode_id if episode else None, old, new, event,
        list(reasons), list(refs)]), session=session,
        economic_episode_id=episode.economic_episode_id if episode else None,
        from_state=old, to_state=new, event_type=event,
        reason_codes=list(reasons), evidence_refs=list(refs))
    if item.transition_id not in {x.transition_id for x in transitions}:
        transitions.append(item)


def _support_for(facts_by_day, session, episode):
    return next((f for f in facts_by_day.get(session, [])
                 if f.zone_id == episode.zone_id and f.test_id == episode.test_id), None)


def _all_pass(conditions):
    gates = [c for c in conditions if c.role == "CONFIRMATION"]
    return bool(gates) and all(c.status == "EVALUATED" and c.predicate_value is True for c in gates)


def _current_status(conditions):
    if any(c.predicate_value is None for c in conditions):
        return None
    return all(c.predicate_value is True for c in conditions)


def _required_unknown(conditions):
    return any(c.role != "DIAGNOSTIC" and c.predicate_value is None for c in conditions)


def _day_gap_reasons(day):
    unknowns = [c for c in day.conditions
                if c.role != "DIAGNOSTIC" and c.predicate_value is None]
    if unknowns:
        return list(dict.fromkeys(r for c in unknowns
            for r in (c.reason_codes or ["CONDITION_INPUT_UNKNOWN"])))
    return list(day.reason_codes)


def _gap_details(timeline, current_conditions):
    current_keys = {(c.session, c.condition_id) for c in current_conditions
                    if c.predicate_value is None}
    gaps = []
    for day in timeline:
        for c in day.conditions:
            if c.role == "DIAGNOSTIC" or c.predicate_value is not None:
                continue
            current = (c.session, c.condition_id) in current_keys
            gaps.append(OpportunityEvidenceGap(session=c.session,
                condition_id=c.condition_id, role=c.role,
                reason_codes=c.reason_codes or ["CONDITION_INPUT_UNKNOWN"],
                affected_outputs=(["current_assessment", "coverage"] if current else ["coverage"])))
        if not day.conditions and day.capability_status == CapabilityStatus.PARTIAL:
            gaps.append(OpportunityEvidenceGap(session=day.session,
                condition_id=day.reason_codes[0], role="INPUT",
                reason_codes=day.reason_codes,
                affected_outputs=["current_assessment", "coverage"]))
    return gaps


def _episode_for_detection(symbol, detection, policy_hash, source_identity,
                           indicator_identity, calculation_version, expected):
    support = detection.selected_support
    touch = detection.session
    touch_i = expected.index(touch)
    deadline = expected[min(touch_i+3, len(expected)-1)]
    # Market-event identity deliberately excludes zone/policy/algorithm IDs.
    # A revised support object for the same ticker/family/touch remains the
    # same economic episode; opportunity_id below distinguishes its evidence.
    economic = "sha256:"+_hash([symbol, touch])
    opportunity = "sha256:"+_hash([economic, policy_hash, source_identity,
        indicator_identity, support.zone_id, support.test_id,
        support.zone_lower, support.zone_upper,
        support.anchor_atr, support.invalidation_line, calculation_version])
    return OpportunityEpisode(economic_episode_id=economic,
        opportunity_id=opportunity, family=detection.family, setup_date=touch, touch_date=touch,
        confirmation_deadline=deadline, state=OpportunityStateName.WATCH,
        zone_id=support.zone_id, test_id=support.test_id,
        zone_lower=support.zone_lower, zone_upper=support.zone_upper,
        anchor_atr=support.anchor_atr, invalidation_line=support.invalidation_line,
        zone_available_at=support.zone_available_at,
        recent_high=detection.recent_high,
        recent_high_session=detection.recent_high_session,
        shallow_state=getattr(detection, "next_state", None),
        reason_codes=[f"{detection.family}_DISCOVERED", "FIXED_SUPPORT_BOUND_AT_TOUCH"])


def evaluate_opportunity_state(input: OpportunityInput) -> EntryOpportunity:
    """Evaluate a complete typed prefix; storage and data access stay outside."""
    ctx, view, policy = input.call_context, input.feature_view, input.effective_policy
    requested_session, request_semantics = _resolve_requested_session(ctx, input.calendar)
    if view.symbol != ctx.symbol:
        raise ValueError("OPPORTUNITY_SYMBOL_MISMATCH")
    if not ctx.effective_daily_session:
        raise ValueError("OPPORTUNITY_EFFECTIVE_SESSION_REQUIRED")
    if view.input_kind == "VERIFIED_CANONICAL" and (
            not view.source.validated or not view.source.sha256 or not view.source.record_identity):
        raise ValueError("OPPORTUNITY_UNVERIFIED_SOURCE")
    if view.input_kind == "TEST" and view.source.source_kind != "TEST":
        raise ValueError("OPPORTUNITY_TEST_IDENTITY_REQUIRED")
    if not view.indicator_identity or not view.price_basis or not view.corporate_action_version:
        raise ValueError("OPPORTUNITY_INPUT_IDENTITY_MISSING")
    expected_all = list(view.expected_sessions)
    if expected_all != sorted(set(expected_all)) or ctx.effective_daily_session not in expected_all:
        raise ValueError("OPPORTUNITY_EXPECTED_SESSIONS_INVALID")
    expected = [s for s in expected_all if view.analysis_start <= s <= ctx.effective_daily_session]
    if not expected:
        raise ValueError("OPPORTUNITY_ANALYSIS_RANGE_EMPTY")
    bars = sorted(view.bars, key=lambda b: b.session)
    if [b.session for b in bars] != sorted(set(b.session for b in bars)):
        raise ValueError("OPPORTUNITY_DATES_NOT_UNIQUE")
    bar_by_day = {b.session.isoformat(): b for b in bars}
    facts_by_day = {}
    for fact in input.support_facts:
        facts_by_day.setdefault(fact.session, []).append(fact)
    source_identity = _source_identity(view)
    policy_hash = _policy_identity(policy, input.shallow_policy)
    support_identity_all = "sha256:"+_hash({"results": input.support_result_ids,
        "facts": [f.model_dump(mode="json") for f in input.support_facts]})

    diagnostics = []
    compatible = False
    if input.prior_state is not None:
        p = input.prior_state
        current_prior_prefix = "sha256:"+_hash([b.model_dump(mode="json") for b in bars
            if p.evaluated_through and b.session.isoformat() <= p.evaluated_through])
        current_prior_support = "sha256:"+_hash({
            "results": {s: rid for s, rid in input.support_result_ids.items()
                        if p.evaluated_through and s <= p.evaluated_through},
            "facts": [f.model_dump(mode="json") for f in input.support_facts
                      if p.evaluated_through and f.session <= p.evaluated_through]})
        compatible = (p.symbol == ctx.symbol and p.source_identity == source_identity and
            p.policy_sha256 == policy_hash and p.indicator_identity == view.indicator_identity and
            p.price_basis == view.price_basis and
            p.corporate_action_version == view.corporate_action_version and
            p.input_prefix_sha256 == current_prior_prefix and
            p.support_identity == current_prior_support)
        if compatible:
            diagnostics.append("PRIOR_STATE_COMPATIBLE_INCREMENTAL")
        else:
            prior_start = p.analysis_start
            if prior_start and view.analysis_start > prior_start:
                raise ValueError("OPPORTUNITY_REPLAY_PREFIX_REQUIRED")
            diagnostics.append("PRIOR_STATE_INVALIDATED_FULL_REPLAY")

    if (compatible and input.prior_state and not input.prior_timeline and
            view.analysis_start <= (input.prior_state.analysis_start or view.analysis_start)):
        # A legacy checkpoint has no detailed committed events.  Rebuild the
        # supplied full prefix once; sliding-window continuation below uses the
        # checkpoint directly and processes only later sessions.
        diagnostics[:] = ["PRIOR_STATE_COMPATIBLE_REPLAY_VERIFIED"]
        compatible = False

    if compatible and not input.prior_timeline and input.prior_state.evaluated_through:
        raise ValueError("OPPORTUNITY_PRIOR_DETAILS_REQUIRED")
    display_sessions = list(expected)
    analysis_start = (input.prior_state.analysis_start if compatible and
                      input.prior_state.analysis_start else view.analysis_start)
    if compatible:
        if (input.prior_state.evaluated_through not in expected_all or
                input.prior_state.evaluated_through > ctx.effective_daily_session or
                analysis_start not in expected_all):
            raise ValueError("OPPORTUNITY_RESUME_SESSION_COVERAGE_INVALID")
        expected = [s for s in expected_all if analysis_start <= s <= ctx.effective_daily_session]

    episodes: list[OpportunityEpisode] = (deepcopy(input.prior_state.episodes)
        if compatible and input.prior_state else [])
    boundary = input.prior_state.evaluated_through if compatible else None
    timeline: list[OpportunityDay] = (deepcopy(committed_days(input.prior_timeline, boundary)) if compatible else [])
    transitions: list[OpportunityTransition] = (deepcopy([t for t in input.prior_transitions
        if boundary and t.session <= boundary]) if compatible else [])
    detections = (deepcopy([d for d in input.prior_detections
        if boundary and d.session <= boundary]) if compatible else [])
    setup_evidence = (deepcopy([e for e in input.prior_setup_evidence
        if boundary and e.session <= boundary]) if compatible else [])
    if compatible and [d.session for d in timeline] != [s for s in expected if s <= boundary]:
        raise ValueError("OPPORTUNITY_PRIOR_TIMELINE_INCOMPLETE")
    active = episodes[-1] if episodes else None
    evaluated_through = input.prior_state.evaluated_through if compatible and input.prior_state else None
    missing_sessions = []
    global_reasons = []
    processed_sessions = []

    process_sessions = pending_sessions(expected, analysis_start, ctx.effective_daily_session, evaluated_through)
    for session in process_sessions:
        processed_sessions.append(session)
        bar = bar_by_day.get(session)
        support_snapshot_known = session in input.support_result_ids
        if bar is None or not _valid_price_bar(bar) or not support_snapshot_known:
            missing_sessions.append(session)
            why = ("DAILY_BAR_MISSING" if bar is None else
                   "DAILY_OHLC_INVALID" if not _valid_price_bar(bar) else
                   "SUPPORT_SNAPSHOT_MISSING")
            global_reasons.append(why)
            last = active.state if active else (episodes[-1].state if episodes else None)
            timeline.append(OpportunityDay(session=session, state=last,
                capability_status=CapabilityStatus.PARTIAL,
                economic_episode_id=active.economic_episode_id if active else None,
                opportunity_id=active.opportunity_id if active else None,
                setup_date=active.setup_date if active else None,
                touch_date=active.touch_date if active else None,
                confirmation_deadline=active.confirmation_deadline if active else None,
                confirmation_date=active.confirmation_date if active else None,
                entry_start=active.entry_start if active else None,
                entry_end=active.entry_end if active else None, eligible=None,
                support_zone_id=active.zone_id if active else None,
                support_test_id=active.test_id if active else None,
                conditions=[], reason_codes=[why, "PROCESSING_STOPPED_AT_FIRST_GAP"]))
            missing_sessions.extend(s for s in expected[expected.index(session)+1:]
                                    if s not in missing_sessions)
            break

        depth_evidence = None
        if policy.family == "SHALLOW_PULLBACK":
            shallow_input = ShallowPullbackInput(call_context=ctx.model_copy(update={
                "effective_daily_session": session}), feature_view=view,
                support_facts=facts_by_day.get(session, []), effective_policy=input.shallow_policy,
                shared_policy=policy, calendar=input.calendar)
            detection = detect_shallow_pullback(shallow_input)
            setup_evidence.append(detection)
            if active and active.shallow_state and active.state not in {
                    OpportunityStateName.INVALIDATED, OpportunityStateName.EXPIRED}:
                depth_evidence = detect_shallow_pullback(shallow_input.model_copy(update={
                    "prior_state": active.shallow_state}))
                setup_evidence.append(depth_evidence)
                active = active.model_copy(update={"shallow_state": depth_evidence.next_state})
                episodes[-1] = active
            else:
                depth_evidence = detection
            detections.append(OpportunityDetection.model_validate(
                detection.model_dump(include=set(OpportunityDetection.model_fields))))
        else:
            detection = detect_healthy_pullback(bar=bar, history=bars,
                support_facts=facts_by_day.get(session, []), policy=policy)
            detections.append(detection)
        day_conditions = list(detection.conditions)
        day_reasons = []
        eligible = False

        if active is not None and active.state in {
                OpportunityStateName.EXPIRED, OpportunityStateName.INVALIDATED}:
            candidate = (_episode_for_detection(ctx.symbol, detection, policy_hash,
                source_identity, view.indicator_identity, _IMPLEMENTATION_ID,
                expected_all) if detection.detected is True else None)
            if candidate is not None and candidate.economic_episode_id != active.economic_episode_id:
                active = candidate
                episodes.append(active)
                _transition(transitions, session, active, None,
                    OpportunityStateName.WATCH, "NEW_INDEPENDENT_SETUP_DISCOVERED",
                    ["NEW_SUPPORT_TEST_REQUIRED_AFTER_TERMINAL_EVENT"],
                    [active.zone_id, active.test_id])
            else:
                day_reasons.append("TERMINAL_EVENT_DOES_NOT_REVIVE")
                timeline.append(OpportunityDay(session=session, state=active.state,
                    capability_status=CapabilityStatus.COMPLETED,
                    economic_episode_id=active.economic_episode_id,
                    opportunity_id=active.opportunity_id, setup_date=active.setup_date,
                    touch_date=active.touch_date,
                    confirmation_deadline=active.confirmation_deadline,
                    confirmation_date=active.confirmation_date,
                    entry_start=active.entry_start, entry_end=active.entry_end,
                    eligible=False, support_zone_id=active.zone_id,
                    support_test_id=active.test_id, conditions=day_conditions,
                    reason_codes=day_reasons))
                evaluated_through = session
                continue

        if active is None and detection.detected is True:
            active = _episode_for_detection(ctx.symbol, detection, policy_hash,
                source_identity, view.indicator_identity, _IMPLEMENTATION_ID,
                expected_all)
            episodes.append(active)
            _transition(transitions, session, active, None, OpportunityStateName.WATCH,
                "SETUP_DISCOVERED", active.reason_codes,
                [active.zone_id, active.test_id])
            day_reasons.extend(active.reason_codes)
        elif active is None and detection.detected is None:
            day_reasons.extend(list(dict.fromkeys(detection.reason_codes +
                ["REQUIRED_DISCOVERY_EVIDENCE_UNKNOWN"])))
            global_reasons.append("REQUIRED_DISCOVERY_EVIDENCE_UNKNOWN")
            timeline.append(OpportunityDay(session=session, state=None,
                capability_status=CapabilityStatus.PARTIAL,
                economic_episode_id=None, opportunity_id=None, setup_date=None,
                touch_date=None, confirmation_deadline=None, confirmation_date=None,
                entry_start=None, entry_end=None, eligible=None,
                support_zone_id=None, support_test_id=None,
                conditions=day_conditions, reason_codes=day_reasons))
            evaluated_through = session
            continue
        elif active is None:
            state = OpportunityStateName.NO_SETUP
            day_reasons.extend(detection.reason_codes)
            timeline.append(OpportunityDay(session=session, state=state,
                capability_status=CapabilityStatus.COMPLETED,
                economic_episode_id=None, opportunity_id=None, setup_date=None,
                touch_date=None, confirmation_deadline=None, confirmation_date=None,
                entry_start=None, entry_end=None, eligible=False,
                support_zone_id=None, support_test_id=None,
                conditions=day_conditions, reason_codes=day_reasons))
            evaluated_through = session
            continue

        old_state = active.state
        current_fact = _support_for(facts_by_day, session, active)
        prior_bar = next((b for b in reversed(bars) if b.session < bar.session), None)
        touch_i, current_i = expected_all.index(active.touch_date), expected_all.index(session)

        # Compute both sides of same-day evidence before applying priority.
        confirmation = []
        if current_i > touch_i and active.confirmation_date is None:
            confirmation = confirmation_conditions(bar=bar, previous_bar=prior_bar,
                history=bars, zone_upper=active.zone_upper, anchor_atr=active.anchor_atr,
                zone_id=active.zone_id, test_id=active.test_id,
                support_fact=current_fact, policy=policy)
            if policy.family == "SHALLOW_PULLBACK":
                prior_twenty = expected_all[max(0, current_i-20):current_i]
                recorded = {b.session.isoformat() for b in bars}
                if len(prior_twenty) != 20 or any(s not in recorded for s in prior_twenty):
                    confirmation = [c.model_copy(update={"predicate_value": None, "status": "UNKNOWN",
                        "left_value": None,
                        "reason_codes": ["RVOL_CALENDAR_PREFIX_INCOMPLETE"]})
                        if c.condition_id == "RVOL20" else c for c in confirmation]
            day_conditions.extend(confirmation)
            if depth_evidence:
                depth_conditions = [c.model_copy(update={"role": "CONFIRMATION"})
                    for c in depth_evidence.conditions if c.condition_id in {
                        "SHALLOW_CUMULATIVE_DEPTH", "CUMULATIVE_LOW_COVERAGE"}]
                confirmation.extend(depth_conditions)
                day_conditions.extend(depth_conditions)

        close_break = float(bar.close) < active.invalidation_line
        support_break = current_fact is not None and current_fact.broken_at is not None and current_fact.broken_at <= session
        structure_break = bar.structure_state == "bearish"
        depth_break = bool(active.shallow_state and active.shallow_state.first_depth_exceeded and
            (active.entry_end is None or session <= active.entry_end))
        invalidated = close_break or support_break or structure_break or depth_break
        day_conditions.extend([
            _invalidation_condition(session, "CLOSE_BELOW_FIXED_INVALIDATION", bar.close,
                "<", active.invalidation_line, close_break, [f"bar:{session}", active.zone_id]),
            _invalidation_condition(session, "SUPPORT_ZONE_BROKEN", support_break,
                "==", True, support_break, [active.zone_id]),
            _invalidation_condition(session, "STRUCTURE_BEARISH_INVALIDATION", bar.structure_state,
                "==", "bearish", structure_break, [f"structure:{session}"]),
        ])
        if active.shallow_state:
            day_conditions.append(_invalidation_condition(session, "SETUP_DEPTH_EXCEEDED",
                active.shallow_state.current_depth_atr, ">", input.shallow_policy.maximum_depth_atr,
                depth_break, [depth_evidence.result_id]))

        if invalidated:
            active = active.model_copy(update={"state": OpportunityStateName.INVALIDATED,
                "invalidation_scope": "STRUCTURE_OR_SUPPORT" if close_break or support_break or structure_break
                    else "SETUP_QUALIFICATION",
                "terminal_date": session,
                "reason_codes": list(dict.fromkeys(active.reason_codes+[
                    "INVALIDATION_PRIORITY_OVER_CONFIRMATION"] +
                    (["CLOSE_BELOW_FIXED_INVALIDATION_LINE"] if close_break else [])+
                    (["SUPPORT_ZONE_BROKEN"] if support_break else [])+
                    (["STRUCTURE_BEARISH"] if structure_break else [])+
                    (["SETUP_DEPTH_EXCEEDED"] if depth_break else [])))})
            episodes[-1] = active
            day_reasons.extend(active.reason_codes)
            _transition(transitions, session, active, old_state, active.state,
                "OPPORTUNITY_INVALIDATED", day_reasons,
                [active.zone_id, active.test_id])
        elif active.confirmation_date is None and current_i > touch_i + policy.confirmation_sessions:
            active = active.model_copy(update={"state": OpportunityStateName.EXPIRED,
                "terminal_date": session,
                "reason_codes": list(dict.fromkeys(active.reason_codes+[
                    "CONFIRMATION_WINDOW_EXPIRED"]))})
            episodes[-1] = active
            day_reasons.append("CONFIRMATION_WINDOW_EXPIRED")
            _transition(transitions, session, active, old_state, active.state,
                "CONFIRMATION_EXPIRED", day_reasons)
        elif active.confirmation_date is None:
            if current_i == touch_i:
                new_state = OpportunityStateName.WATCH
                day_reasons.append("TOUCH_DAY_NOT_CONFIRMABLE")
            elif _all_pass(confirmation):
                confirmation_date = session
                c_i = expected_all.index(session)
                entry_start = expected_all[c_i+1] if c_i+1 < len(expected_all) else None
                entry_end = expected_all[c_i+policy.entry_window_sessions] if c_i+policy.entry_window_sessions < len(expected_all) else None
                new_state = OpportunityStateName.ENTRY_READY
                active = active.model_copy(update={"state": new_state,
                    "confirmation_date": confirmation_date,
                    "entry_start": entry_start, "entry_end": entry_end,
                    "reason_codes": list(dict.fromkeys(active.reason_codes+[
                        "FIRST_CONFIRMATION_FROZEN", "CONFIRMATION_DAY_NOT_ENTRY_DAY"]))})
                episodes[-1] = active
                day_reasons.extend(["FIRST_CONFIRMATION_FROZEN", "CONFIRMATION_DAY_NOT_ENTRY_DAY"])
                _transition(transitions, session, active, old_state, new_state,
                    "FIRST_CONFIRMATION", day_reasons,
                    [active.zone_id, active.test_id])
            else:
                new_state = OpportunityStateName.CONFIRMING
                active = active.model_copy(update={"state": new_state})
                episodes[-1] = active
                day_reasons.extend(["CONFIRMATION_CONDITIONS_PENDING"] +
                    [r for c in confirmation if c.predicate_value is not True for r in c.reason_codes])
                if old_state != new_state:
                    _transition(transitions, session, active, old_state, new_state,
                        "CONFIRMATION_PENDING", day_reasons)
        else:
            current_i = expected_all.index(session)
            end_i = expected_all.index(active.entry_end) if active.entry_end in expected_all else None
            if end_i is not None and current_i > end_i:
                active = active.model_copy(update={"state": OpportunityStateName.EXPIRED,
                    "terminal_date": session,
                    "reason_codes": list(dict.fromkeys(active.reason_codes+[
                        "ENTRY_WINDOW_EXPIRED"]))})
                episodes[-1] = active
                day_reasons.append("ENTRY_WINDOW_EXPIRED")
                _transition(transitions, session, active, old_state, active.state,
                    "ENTRY_WINDOW_EXPIRED", day_reasons)
            else:
                current = current_eligibility_conditions(bar=bar,
                    zone_upper=active.zone_upper, support_fact=current_fact, policy=policy)
                if depth_evidence:
                    current.extend(c.model_copy(update={"role": "CURRENT_ELIGIBILITY"})
                        for c in depth_evidence.conditions if c.condition_id in {
                            "SHALLOW_CUMULATIVE_DEPTH", "CUMULATIVE_LOW_COVERAGE"})
                day_conditions.extend(current)
                active = active.model_copy(update={"state": OpportunityStateName.ENTRY_READY})
                episodes[-1] = active
                if session == active.confirmation_date:
                    eligible = False
                    day_reasons.append("CONFIRMATION_SESSION_NOT_ENTRY_SESSION")
                elif active.entry_start and active.entry_end and active.entry_start <= session <= active.entry_end:
                    eligible = _current_status(current)
                    if eligible is False:
                        day_reasons.append("CURRENT_ENTRY_CONDITIONS_NOT_SATISFIED")
                        failed = [c.condition_id for c in current if c.predicate_value is False]
                        event = ("ENTRY_OVERDISTANCE" if "CURRENT_DISTANCE_FROM_FIXED_ZONE" in failed
                                 else "CURRENT_ENTRY_INELIGIBLE")
                        _transition(transitions, session, active,
                            OpportunityStateName.ENTRY_READY,
                            OpportunityStateName.ENTRY_READY, event, failed,
                            [active.zone_id, active.test_id])
                    elif eligible is None:
                        day_reasons.append("CURRENT_ENTRY_CONDITIONS_UNKNOWN")
                    else:
                        prior_day = timeline[-1] if timeline else None
                        event = ("ENTRY_REQUALIFIED_WITHIN_FIXED_WINDOW"
                            if prior_day and prior_day.opportunity_id == active.opportunity_id
                            and prior_day.eligible is False else "ENTRY_WINDOW_ELIGIBLE")
                        _transition(transitions, session, active,
                            OpportunityStateName.ENTRY_READY,
                            OpportunityStateName.ENTRY_READY, event,
                            ["CURRENT_ENTRY_CONDITIONS_SATISFIED"],
                            [active.zone_id, active.test_id])
                else:
                    eligible = False
                    day_reasons.append("OUTSIDE_ENTRY_WINDOW")

        capability = (CapabilityStatus.PARTIAL if _required_unknown(day_conditions)
                      else CapabilityStatus.COMPLETED)
        if capability == CapabilityStatus.PARTIAL:
            global_reasons.append("CONDITION_INPUT_UNKNOWN")
        timeline.append(OpportunityDay(session=session, state=active.state,
            capability_status=capability,
            economic_episode_id=active.economic_episode_id,
            opportunity_id=active.opportunity_id, setup_date=active.setup_date,
            touch_date=active.touch_date,
            confirmation_deadline=active.confirmation_deadline,
            confirmation_date=active.confirmation_date,
            entry_start=active.entry_start, entry_end=active.entry_end,
            eligible=eligible, support_zone_id=active.zone_id,
            support_test_id=active.test_id, conditions=day_conditions,
            reason_codes=list(dict.fromkeys(day_reasons))))
        evaluated_through = session

    actual = [s for s in expected if s in bar_by_day and _valid_price_bar(bar_by_day[s])]
    prefix_bars = [b.model_dump(mode="json") for b in bars
                   if evaluated_through and b.session.isoformat() <= evaluated_through]
    used_support_results = {s: rid for s, rid in input.support_result_ids.items()
                            if evaluated_through and s <= evaluated_through}
    used_facts = [f.model_dump(mode="json") for f in input.support_facts
                  if evaluated_through and f.session <= evaluated_through]
    support_identity = "sha256:"+_hash({"results": used_support_results, "facts": used_facts})
    prefix_hash = "sha256:"+_hash(prefix_bars)
    if evaluated_through:
        new_known = len([s for s in process_sessions if s <= evaluated_through])
    else:
        new_known = 0
    state_revision = ((input.prior_state.state_revision if compatible and input.prior_state else 0)
                      + new_known)
    checkpoint = OpportunityStateCheckpoint(symbol=ctx.symbol, episodes=episodes,
        evaluated_through=evaluated_through,
        state_revision=state_revision, input_prefix_sha256=prefix_hash,
        source_identity=source_identity, support_identity=support_identity,
        policy_sha256=policy_hash, indicator_identity=view.indicator_identity,
        price_basis=view.price_basis,
        corporate_action_version=view.corporate_action_version,
        analysis_start=(input.prior_state.analysis_start if compatible and input.prior_state
                        and input.prior_state.analysis_start else view.analysis_start))
    last_day = timeline[-1] if timeline else None
    current_episode = next((e for e in reversed(episodes)
                            if last_day and e.economic_episode_id == last_day.economic_episode_id), None)
    current_conditions = last_day.conditions if last_day else []
    discovery_unresolved = bool(last_day and last_day.state is None and
        any(c.role == "DISCOVERY" and c.predicate_value is None for c in current_conditions))
    relevant = [c for c in current_conditions if c.role != "DIAGNOSTIC" and
        (c.role != "DISCOVERY" or discovery_unresolved or
         (current_episode is not None and c.session == current_episode.setup_date))]
    supporting = [c.condition_id for c in relevant
        if ((c.role == "INVALIDATION" and c.predicate_value is False) or
            (c.role != "INVALIDATION" and c.predicate_value is True))]
    opposing = [c.condition_id for c in relevant
        if ((c.role == "INVALIDATION" and c.predicate_value is True) or
            (c.role != "INVALIDATION" and c.predicate_value is False))]
    missing = [c.condition_id for c in relevant if c.predicate_value is None]
    coverage_gaps = _gap_details(timeline, relevant)
    current_gaps = [g for g in coverage_gaps if "current_assessment" in g.affected_outputs]
    missing = list(dict.fromkeys(missing + [g.condition_id for g in current_gaps]))
    # Rebuild coverage from the retained committed prefix and freshly evaluated
    # days. A resolved gap report beyond the checkpoint is never inherited.
    global_reasons = list(dict.fromkeys(
        reason for d in timeline if d.capability_status == CapabilityStatus.PARTIAL
        for reason in _day_gap_reasons(d)))
    status = CapabilityStatus.PARTIAL if missing_sessions or global_reasons else CapabilityStatus.COMPLETED
    semantic = {"symbol": ctx.symbol, "as_of": ctx.effective_daily_session,
        "requested_session": requested_session, "request_semantics": request_semantics,
        "implementation": _IMPLEMENTATION_ID,
        "calculation_version": "entry-opportunity-v2.2",
        "policy": policy_hash, "source": source_identity, "support": support_identity,
        "indicator": view.indicator_identity, "price_basis": view.price_basis,
        "corporate_action_version": view.corporate_action_version,
        "legal_input": prefix_hash,
        "episodes": [e.model_dump(mode="json") for e in episodes],
        "timeline": [d.model_dump(mode="json") for d in timeline],
        "transitions": [t.model_dump(mode="json") for t in transitions],
        "evaluated_through": evaluated_through}
    result_id = "sha256:"+_hash(semantic)
    checkpoint = checkpoint.model_copy(update={"committed_result_id": result_id})
    coverage = OpportunityCoverage(expected_sessions=expected,
        actual_sessions=actual, missing_sessions=missing_sessions,
        analysis_start=analysis_start, evaluated_through=evaluated_through,
        display_sessions=display_sessions, processed_sessions=processed_sessions,
        indicator_seed_start=view.indicator_seed_start,
        indicator_identity=view.indicator_identity, legal_input_sha256=prefix_hash,
        source_identity=source_identity, support_identity=support_identity,
        fields={"OHLC": CapabilityStatus.COMPLETED if not missing_sessions else CapabilityStatus.PARTIAL,
                "VOLUME": CapabilityStatus.PARTIAL if any(b.volume is None for b in bars if b.session.isoformat() in actual) else CapabilityStatus.COMPLETED,
                "SUPPORT": CapabilityStatus.COMPLETED if all(s in input.support_result_ids for s in actual) else CapabilityStatus.PARTIAL},
        reason_codes=list(dict.fromkeys(global_reasons)))
    legacy = deepcopy(input.legacy_opinion)
    differences = [{"field": "decision_authority", "legacy": "UNCHANGED",
        "v2": "OBSERVATION_ONLY", "reason_code": "V2_DOES_NOT_CHANGE_PRODUCTION_ACTION"}]
    differences.append({"field": "pullback_and_opportunity_state",
        "legacy": legacy.get("pullback_state"),
        "legacy_execution_status": legacy.get("execution_status", "NOT_RECORDED"),
        "v2": last_day.state.value if last_day and last_day.state else None,
        "reason_code": "LEGACY_PULLBACK_CLASSIFICATION_IS_NOT_V2_LIFECYCLE_STATE"})
    upstream_ids = list(dict.fromkeys(f.support_result_id for f in input.support_facts
        if evaluated_through and f.session <= evaluated_through))
    confirmation_elapsed = (requested_session > current_episode.confirmation_deadline
        if current_episode and current_episode.confirmation_date is None else
        False if current_episode else None)
    entry_elapsed = (requested_session > current_episode.entry_end
        if current_episode and current_episode.entry_end else False if current_episode else None)
    if not last_day or missing_sessions:
        requested_eligible = None
    elif (last_day.state == OpportunityStateName.NO_SETUP and
          last_day.capability_status == CapabilityStatus.COMPLETED):
        requested_eligible = False
    elif not current_episode:
        requested_eligible = None
    else:
        requested_eligible = requested_applicability(requested=requested_session,
            evidence=ctx.effective_daily_session, semantics=request_semantics,
            eligible=last_day.eligible, entry_end=current_episode.entry_end)
    return EntryOpportunity(symbol=ctx.symbol, as_of=ctx.effective_daily_session,
        family=policy.family, shallow_pullback_timeline=setup_evidence,
        active_families=[policy.family] if requested_eligible is True else [],
        status=status, state=last_day.state if last_day else None,
        last_known_state=last_day.state if last_day else None,
        evaluated_through=evaluated_through,
        last_known_session=evaluated_through,
        entry_permitted_from=current_episode.entry_start if current_episode else None,
        entry_permitted_until=current_episode.entry_end if current_episode else None,
        confirmation_deadline_elapsed_at_requested_session=confirmation_elapsed,
        entry_window_elapsed_at_requested_session=entry_elapsed,
        eligible_at_requested_time=requested_eligible,
        requested_session=requested_session, request_time_semantics=request_semantics,
        economic_episode_id=current_episode.economic_episode_id if current_episode else None,
        opportunity_id=current_episode.opportunity_id if current_episode else None,
        result_id=result_id, matched_families=[policy.family] if episodes else [],
        upstream_result_ids=upstream_ids,
        run_id=ctx.run_id, request_id=ctx.request_id, received_at=view.received_at,
        effective_policy=policy, policy_sha256=policy_hash, call_context=ctx,
        episodes=episodes, timeline=timeline, transitions=transitions,
        detections=detections, current_conditions=current_conditions,
        supporting_evidence=supporting, opposing_evidence=opposing,
        missing_evidence=missing, current_missing_details=current_gaps,
        coverage_missing_evidence=coverage_gaps,
        next_observation_conditions=_next_conditions(last_day),
        legacy_opinion=legacy, opinion_differences=differences, coverage=coverage,
        next_state=checkpoint, call_diagnostics=diagnostics,
        provenance=[view.source, *view.auxiliary_sources],
        explanation=_explanation(last_day, status, evaluated_through))


def _invalidation_condition(session, condition_id, left, operator, right, predicate, refs):
    from pcs.trend.selection_models import OpportunityCondition
    return OpportunityCondition(condition_id=condition_id, session=session,
        role="INVALIDATION", left_value=left, operator=operator,
        right_value=right, unit="boolean" if isinstance(left, bool) else None,
        predicate_value=bool(predicate), status="EVALUATED",
        source_refs=list(refs), reason_codes=[])


def _next_conditions(day):
    if day is None:
        return ["需要合法日线与同日支撑快照"]
    if day.capability_status != CapabilityStatus.COMPLETED:
        return ["补齐首个缺失交易日后按顺序重放"]
    if day.state == OpportunityStateName.WATCH:
        return ["下一交易日起检查确认条件，最晚至 confirmation_deadline"]
    if day.state == OpportunityStateName.CONFIRMING:
        return ["在原 confirmation_deadline 前满足全部确认条件"]
    if day.state == OpportunityStateName.ENTRY_READY:
        return ["仅在原 entry_start 至 entry_end 内复核支撑、结构和距离；ENTRY_READY不是下单授权"]
    if day.state in {OpportunityStateName.EXPIRED, OpportunityStateName.INVALIDATED}:
        return ["同一事件不复活；须有新的离开及独立回踩事件"]
    return ["等待新的健康回调与合法支撑触及"]


def _explanation(day, status, evaluated):
    if day is None:
        return "没有可评估的交易日；能力未知，不解释为NO_SETUP。"
    return (f"评估至{evaluated}，业务状态{day.state.value if day.state else '未知'}，"
            f"能力状态{status.value}。这是描述性v2意见，不是下单授权或期权评估。")
