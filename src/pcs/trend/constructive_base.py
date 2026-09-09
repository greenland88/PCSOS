"""Causal constructive-base observations over already prepared authoritative fields."""
from __future__ import annotations

from copy import deepcopy
from statistics import median
import math
import pandas as pd
import exchange_calendars as xc

from pcs.analysis_contracts import CapabilityStatus
from pcs.trend.selection_models import (
    BaseBoundary, BaseFormationCandidate, BaseEvent, BaseDay, BaseState, BaseResult,
    BaseBreakoutRelationship, ConstructiveBaseInput, OpportunityEvidenceGap,
    OpportunityStateName, OpportunityTransition, OpportunityCoverage, OpportunitySupportFact,
    SupportSourceAnchor, SupportFeatureBar, EntryOpportunity, OpportunityDay,
    OpportunityEpisode, OpportunityStateCheckpoint, BaseStructureResolution,
)
from pcs.trend.breakout_retest import _hash
from pcs.trend.lifecycle import pending_sessions, committed_days, required_conjunction, requested_applicability
from pcs.trend.opportunity_state import _resolve_requested_session, _valid_price_bar
from pcs.trend.setup_detectors import _condition, confirmation_conditions, current_eligibility_conditions
from pcs.trend.support_zones import create_fixed_support_zone, update_fixed_support_zone, describe_retrospective_tests

TERMINAL = {OpportunityStateName.EXPIRED, OpportunityStateName.INVALIDATED}
OPPORTUNITY_KEYS = ('calculation_version', 'reclaim_buffer_atr', 'confirmation_sessions',
    'entry_window_sessions', 'minimum_close_location', 'minimum_rvol20',
    'maximum_entry_distance_atr', 'upper_wick_rejection_atr', 'upper_rejection_close_location', 'upper_wick_role')


def _support_bar(bar):
    return SupportFeatureBar(**{k: getattr(bar, k) for k in SupportFeatureBar.model_fields})


def _positive(value):
    return value is not None and math.isfinite(value) and value > 0


def _valid_bar(bar):
    return (_valid_price_bar(bar) and bar.low > 0 and
        bar.high >= max(bar.open, bar.close) and bar.low <= min(bar.open, bar.close))


def _gap(session, condition, role, refs, reasons=None, affected=None):
    return OpportunityEvidenceGap(session=session, condition_id=condition, role=role,
        evidence_refs=refs, reason_codes=reasons or [condition], affected_outputs=affected or ['coverage'])


def _resolve_structure(bar, detail, view):
    """Existing authority order, uniformly applied to all platform gates."""
    session = bar.session.isoformat()
    bar_refs = [view.source.source_id, view.indicator_identity, f'feature_bar.structure:{session}']
    selected, origin, refs = bar.structure_state, 'FEATURE_BAR', bar_refs
    if detail is not None:
        detail_refs = [detail.source.source_id, detail.calculation_version, f'base_structure_evidence:{session}']
        if detail.high_comparison == detail.low_comparison == 'lower':
            selected, origin, refs = 'bearish', 'DETAIL_LH_LL', detail_refs
        elif detail.structure_state is not None:
            selected, origin, refs = detail.structure_state, 'DETAIL_STATE', detail_refs
    overridden = []
    if origin != 'FEATURE_BAR' and bar.structure_state is not None and bar.structure_state != selected:
        overridden.append('FEATURE_BAR')
    if origin == 'DETAIL_LH_LL' and detail.structure_state is not None and detail.structure_state != selected:
        overridden.append('DETAIL_STATE')
    reasons = ['BASE_STRUCTURE_AUTHORITY_' + origin]
    if overridden:
        reasons.append('BASE_STRUCTURE_SOURCE_CONFLICT_RESOLVED')
    if selected is None:
        reasons.append('BASE_STRUCTURE_UNKNOWN')
    payload = dict(session=session, selected_value=selected, selected_from=origin,
        selected_source_refs=refs, bar_value=bar.structure_state, bar_source_refs=bar_refs,
        detail=detail.model_dump(mode='json') if detail else None,
        overridden_sources=overridden, reason_codes=reasons)
    return BaseStructureResolution(resolution_id=_hash(payload), **payload)


def _structure(bar, resolutions):
    return resolutions[bar.session.isoformat()].selected_value


def _structure_conditions(conditions, resolutions):
    """Attach actual authority and conflict reasons to every consuming gate."""
    return [c.model_copy(update={
        'source_refs': list(dict.fromkeys(ref for r in resolutions
            for ref in [f'structure_resolution:{r.resolution_id}', *r.selected_source_refs])),
        'reason_codes': list(dict.fromkeys(c.reason_codes + [code for r in resolutions for code in r.reason_codes])),
    }) if c.condition_id in {'BASE_PRECEDING_BULLISH_EXISTS', 'BASE_STRUCTURE_NOT_BEARISH',
        'STRUCTURE_BULLISH_CONFIRMATION', 'STRUCTURE_STILL_BULLISH'} else c for c in conditions]


def _formation(input, session, sessions, bars, details, resolutions, policy_hash, source_hash):
    view, policy = input.feature_view, input.effective_policy
    i = sessions.index(session)
    window = sessions[max(0, i-19):i+1]
    preceding = sessions[max(0, i-39):max(0, i-19)]
    conditions, gaps = [], []
    def gate(cid, left, op, right, predicate, refs=(), role='DISCOVERY'):
        conditions.append(_condition(cid, session, role, left, op, right, predicate=predicate,
            refs=list(refs) or [view.source.source_id, session], reasons=[cid+'_UNKNOWN'] if predicate is None else []))
    complete_w = len(window) == 20 and all(s in bars and _valid_bar(bars[s]) for s in window)
    complete_p = len(preceding) == 20 and all(s in bars and _valid_bar(bars[s]) for s in preceding)
    gate('BASE_FORMATION_CONTIGUOUS_OHLC', len(window), '==', 20, True if complete_w else None)
    gate('BASE_PRECEDING_CONTIGUOUS_OHLC', len(preceding), '==', 20, True if complete_p else None)
    uptrend = [s for s in preceding if s in bars and _structure(bars[s], resolutions) == 'bullish']
    unknown_structure = [s for s in preceding if s not in bars or _structure(bars[s], resolutions) is None]
    for s in preceding + window:
        if s not in bars or not _valid_bar(bars[s]):
            gaps.append(_gap(s, 'BASE_WINDOW_OHLC_MISSING', 'INPUT', [view.source.source_id, f'bar:{s}']))
    for s in unknown_structure:
        gaps.append(_gap(s, 'BASE_PRECEDING_STRUCTURE_UNKNOWN', 'DISCOVERY', [view.indicator_identity, f'structure:{s}']))
    gate('BASE_PRECEDING_BULLISH_EXISTS', len(uptrend), '>', 0,
         True if uptrend else None if unknown_structure or len(preceding) < 20 else False,
         [view.indicator_identity, *[f'structure:{s}' for s in uptrend]])
    state = _structure(bars[session], resolutions)
    gate('BASE_STRUCTURE_NOT_BEARISH', state, '!=', 'bearish', None if state is None else state != 'bearish')
    gate('BASE_STRUCTURE_DETAILS_SAVED', session in details, '==', True,
         True if session in details else None, [view.indicator_identity], role='DIAGNOSTIC')
    if session not in details:
        gaps.append(_gap(session, 'BASE_STRUCTURE_DETAILS_NOT_SAVED', 'DIAGNOSTIC', [view.indicator_identity]))
    bar = bars[session]
    for cid, value, source in [('BASE_HEALTH_DIAGNOSTIC', bar.trend_health, bar.trend_health_source),
                               ('BASE_PHASE_DIAGNOSTIC', bar.short_term_phase, bar.short_term_phase_source)]:
        gate(cid, value, 'RECORDED', True, True if value is not None else None,
             [source or view.indicator_identity], role='DIAGNOSTIC')
    atr = bar.atr14 if _positive(bar.atr14) else None
    gate('BASE_FORMATION_ATR_VALID', atr, '>', 0, True if atr else None)
    lower = min(bars[s].low for s in window) if complete_w else None
    upper = max(bars[s].high for s in window) if complete_w else None
    width = upper-lower if complete_w else None
    gate('BASE_POSITIVE_WIDTH', width, '>', 0, width > 0 if width is not None else None)
    width_atr = width/atr if width is not None and atr else None
    gate('BASE_WIDTH_ATR', width_atr, '<=', policy.maximum_width_atr,
         width_atr <= policy.maximum_width_atr if width_atr is not None else None)
    tr = []
    if complete_w:
        for s in window:
            j = sessions.index(s)
            prev = bars.get(sessions[j-1]) if j else None
            if prev is None or prev.close is None or not math.isfinite(prev.close):
                gaps.append(_gap(s, 'BASE_TR_PREVIOUS_CLOSE_MISSING', 'DISCOVERY', [view.source.source_id, f'bar:{s}']))
                continue
            b = bars[s]
            tr.append(dict(session=s, previous_session=sessions[j-1], previous_close=prev.close,
                high=b.high, low=b.low, true_range=max(b.high-b.low, abs(b.high-prev.close), abs(b.low-prev.close)),
                source_refs=[view.source.source_id, f'bar:{s}', f'bar:{sessions[j-1]}']))
    reference = median(t['true_range'] for t in tr[:15]) if len(tr) == 20 else None
    recent = median(t['true_range'] for t in tr[15:]) if len(tr) == 20 else None
    gate('BASE_TR_CONTRACTION', recent, '<=', reference, recent <= reference if recent is not None else None)
    lows = [s for s in window if complete_w and bars[s].low == lower]
    highs = [s for s in window if complete_w and bars[s].high == upper]
    candidate_id = _hash([view.symbol, session, window, lower, upper, atr, policy_hash, source_hash])
    retrospective = []
    if complete_w and atr and width > 0:
        anchor = SupportSourceAnchor(source_id=_hash([candidate_id, 'BASE_LOWER_BOUNDARY']),
            source_type='BASE_LOWER_BOUNDARY', price=lower, observed_at=session, available_at=session)
        zone = create_fixed_support_zone(view.symbol, anchor, atr, input.support_policy, view)
        retrospective = describe_retrospective_tests(zone, [_support_bar(bars[s]) for s in window],
            sessions, input.support_policy, session)
    held = sum(t.test.status == 'HELD' for t in retrospective) if complete_w and atr and width > 0 else None
    gate('BASE_INDEPENDENT_HELD_TESTS', held, '>=', policy.minimum_held_tests,
         held >= policy.minimum_held_tests if held is not None else None,
         [t.test.test_id for t in retrospective])
    used = [resolutions[s] for s in preceding + [session] if s in resolutions]
    conditions = [_structure_conditions([c], [resolutions[s] for s in preceding if s in resolutions]
        if c.condition_id == 'BASE_PRECEDING_BULLISH_EXISTS' else [resolutions[session]])[0] for c in conditions]
    return BaseFormationCandidate(candidate_id=candidate_id, session=session, structure_resolutions=used,
        preceding_window=preceding, formation_window=window, uptrend_evidence_sessions=uptrend,
        lower=lower, upper=upper, formation_atr=atr, width_atr=width_atr,
        lower_extreme_sessions=lows, upper_extreme_sessions=highs, tr_samples=tr,
        recent_tr_median=recent, reference_tr_median=reference, retrospective_tests=retrospective,
        held_count=held, detected=required_conjunction(conditions), conditions=conditions, missing_details=gaps)


def _event(candidate, parent, input, sessions):
    base_id, f = candidate.candidate_id, candidate.session
    boundaries = []
    for kind, price, extremes in [('BASE_LOWER_BOUNDARY', candidate.lower, candidate.lower_extreme_sessions),
                                  ('BASE_UPPER_BOUNDARY', candidate.upper, candidate.upper_extreme_sessions)]:
        boundaries.append(BaseBoundary(source_id=_hash([base_id, kind]), source_type=kind,
            base_id=base_id, formed_at=f, known_at=f, window=candidate.formation_window,
            price=price, extreme_sessions=extremes, representative_session=extremes[-1],
            source_refs=[input.feature_view.source.source_id, *[f'bar:{s}' for s in extremes]]))
    anchor = SupportSourceAnchor(source_id=boundaries[0].source_id, source_type='BASE_LOWER_BOUNDARY',
        price=candidate.lower, observed_at=f, available_at=f)
    zone = create_fixed_support_zone(input.call_context.symbol, anchor, candidate.formation_atr,
        input.support_policy, input.feature_view)
    return BaseEvent(base_id=base_id, parent_base_id=parent.base_id if parent else None,
        formed_at=f, formation_window=candidate.formation_window, preceding_window=candidate.preceding_window,
        lower=candidate.lower, upper=candidate.upper, formation_atr=candidate.formation_atr,
        lower_boundary=boundaries[0], upper_boundary=boundaries[1], lower_zone=zone,
        retrospective_tests=candidate.retrospective_tests,
        first_touch_deadline=sessions[sessions.index(f)+input.effective_policy.first_live_touch_wait_sessions],
        upside_exit_line=candidate.upper+input.effective_policy.upside_exit_buffer_atr*candidate.formation_atr,
        reason_codes=['BASE_FORMED', 'RETROSPECTIVE_HELD_NOT_LIVE_ENTRY'])


def _fact(event, session):
    test = next((t for t in event.lower_zone.tests if t.test_id == event.live_test_id), None)
    if test is None:
        return None
    z = event.lower_zone
    return OpportunitySupportFact(session=session, support_result_id=event.base_id,
        zone_id=z.zone_id, test_id=test.test_id, zone_lower=z.lower, zone_upper=z.upper,
        anchor_atr=z.anchor_atr, invalidation_line=z.invalidation_line, zone_available_at=z.available_at,
        touch_session=test.touch_session, test_status=test.status, first_held_at=test.first_held_at,
        broken_at=z.broken_at, zone_state=z.state, source_ids=z.observed_source_ids,
        sources=z.creation_sources, reason_codes=test.reason_codes)


def _position(event, bar, policy, role):
    position = (bar.close-event.lower)/(event.upper-event.lower)
    return position, _condition('BASE_LOWER_HALF_ENTRY', bar.session.isoformat(), role,
        position, '<=', policy.maximum_base_position, predicate=position <= policy.maximum_base_position,
        refs=[event.base_id, f'bar:{bar.session}'])


def _snapshot(event):
    if event is None:
        return {}
    return dict(base_id=event.base_id, formed_at=event.formed_at, lower=event.lower, upper=event.upper,
        formation_atr=event.formation_atr, lower_zone_id=event.lower_zone.zone_id,
        first_touch_deadline=event.first_touch_deadline, retest_session=event.retest_session,
        live_test_id=event.live_test_id, confirmation_deadline=event.confirmation_deadline,
        confirmation_session=event.confirmation_session, entry_start=event.entry_start, entry_end=event.entry_end)


def _economic(symbol, day):
    return _hash([symbol, day.lower_zone_id, day.live_test_id]) if day.live_test_id else None


def _transitions(symbol, timeline):
    transitions, old = [], None
    for d in timeline:
        if d.opportunity_state != old:
            transitions.append(OpportunityTransition(transition_id=_hash([d.base_id, d.session, old, d.opportunity_state]),
                session=d.session, economic_episode_id=_economic(symbol, d), from_state=old,
                to_state=d.opportunity_state, event_type='CONSTRUCTIVE_BASE_STATE_CHANGED',
                reason_codes=d.reason_codes, evidence_refs=[x for x in (d.base_id, d.live_test_id) if x]))
            old = d.opportunity_state
    return transitions


def detect_constructive_base(input: ConstructiveBaseInput) -> BaseResult:
    """Observe fixed bases without any I/O, indicator substitution, or trading."""
    ctx, view, policy = input.call_context, input.feature_view, input.effective_policy
    requested, semantics = _resolve_requested_session(ctx, input.calendar)
    day = ctx.effective_daily_session
    if view.symbol != ctx.symbol or not day:
        raise ValueError('BASE_INPUT_IDENTITY_MISMATCH')
    if view.input_kind == 'VERIFIED_CANONICAL' and not (view.source.validated and view.source.sha256 and view.source.record_identity):
        raise ValueError('BASE_UNVERIFIED_SOURCE')
    if view.input_kind == 'TEST' and view.source.source_kind != 'TEST':
        raise ValueError('BASE_TEST_IDENTITY_REQUIRED')
    sessions = list(view.expected_sessions)
    if sessions != sorted(set(sessions)) or day not in sessions or view.analysis_start not in sessions:
        raise ValueError('BASE_SESSION_COVERAGE_INVALID')
    calendar = xc.get_calendar(input.calendar)
    needed = policy.first_live_touch_wait_sessions+input.opportunity_policy.confirmation_sessions+input.opportunity_policy.entry_window_sessions+1
    if len(sessions)-sessions.index(day)-1 < needed:
        sessions += [str(s.date()) for s in calendar.sessions_window(pd.Timestamp(sessions[-1]), needed+1)][1:]
    selected = [b for b in view.bars if b.session.isoformat() <= day]
    dates = [b.session.isoformat() for b in selected]
    if dates != sorted(set(dates)) or any(s not in sessions for s in dates):
        raise ValueError('BASE_BAR_DATES_INVALID')
    bars = dict(zip(dates, selected))
    details = {}
    for d in input.structure_evidence:
        if d.session > day:
            continue
        if d.session in details or not d.source.validated or not d.calculation_version:
            raise ValueError('BASE_STRUCTURE_EVIDENCE_INVALID')
        if any(s.confirmed_at > d.session or s.confirmed_at < s.pivot_date for s in d.confirmed_swings):
            raise ValueError('BASE_STRUCTURE_FUTURE_SWING')
        if any(s.pivot_date in sessions and s.confirmed_at in sessions and
               sessions.index(s.confirmed_at)-sessions.index(s.pivot_date) < input.support_policy.pivot_right_bars
               for s in d.confirmed_swings):
            raise ValueError('BASE_STRUCTURE_CONFIRMATION_TOO_EARLY')
        details[d.session] = d
    resolutions = {s: _resolve_structure(b, details.get(s), view) for s, b in bars.items()}
    # Component-local copies; prefix hashing and other families retain the raw inputs.
    effective_bars = {s: b.model_copy(update={'structure_state': resolutions[s].selected_value}) for s, b in bars.items()}
    opportunity_values = {k: getattr(input.opportunity_policy, k) for k in OPPORTUNITY_KEYS}
    policy_hash = _hash([policy.model_dump(mode='json'), input.support_policy.model_dump(mode='json'), opportunity_values])
    source_hash = _hash([view.source.model_dump(mode='json'), [s.model_dump(mode='json') for s in view.auxiliary_sources],
        view.indicator_identity, view.price_basis, view.corporate_action_version, input.calendar])
    def prefix(through):
        return _hash([[bars[s].model_dump(mode='json') for s in dates if through and s <= through],
            [details[s].model_dump(mode='json') for s in sorted(details) if through and s <= through]])
    prior = input.prior_state
    if prior and (prior.calculation_version != policy.calculation_version or prior.symbol != ctx.symbol or
            prior.policy_identity != policy_hash or prior.source_identity != source_hash or
            prior.evaluated_through and (prior.evaluated_through not in sessions or prior.evaluated_through > day) or
            prior.input_prefix_sha256 != prefix(prior.evaluated_through)):
        raise ValueError('BASE_PRIOR_REPLAY_REQUIRED')
    start = prior.analysis_start if prior else view.analysis_start
    evaluated = prior.evaluated_through if prior else None
    timeline = deepcopy(committed_days(prior.timeline, evaluated)) if prior else []
    if prior and [d.session for d in timeline] != [s for s in sessions if evaluated and start <= s <= evaluated]:
        raise ValueError('BASE_PRIOR_TIMELINE_INCOMPLETE')
    events = deepcopy(prior.base_events) if prior else []
    candidates = deepcopy(prior.formation_candidates) if prior else []
    revision = prior.state_revision if prior else 0
    process = pending_sessions(sessions, start, day, evaluated)
    missing, input_gaps = [], []
    for session in process:
        bar = effective_bars.get(session)
        event = events[-1] if events else None
        if bar is None or not _valid_bar(bar):
            missing = [s for s in process if s >= session and (s not in bars or not _valid_bar(bars[s]))]
            for s in missing:
                why = 'DAILY_BAR_MISSING' if s not in bars else 'DAILY_OHLC_INVALID'
                input_gaps.append(_gap(s, why, 'INPUT', [view.source.source_id, f'bar:{s}'], affected=['current_assessment', 'coverage']))
            timeline.append(BaseDay(session=session, **_snapshot(event), base_detected=True if event else None,
                base_validity='UNKNOWN', opportunity_state=event.state if event else OpportunityStateName.NO_SETUP,
                eligible=None, conditions=[], reason_codes=['PROCESSING_STOPPED_AT_FIRST_GAP']))
            break
        conditions, reasons, eligible, position = [], [], False, None
        detected = True if event else False
        if event and event.state not in TERMINAL:
            zone, _ = update_fixed_support_zone(event.lower_zone, _support_bar(bar), sessions, input.support_policy, [])
            event = event.model_copy(update={'lower_zone': zone})
            structure = _structure(bar, resolutions)
            conditions = [_condition('BASE_FIXED_SUPPORT_VALID', session, 'INVALIDATION', bar.close, '>=', zone.invalidation_line,
                predicate=bar.close >= zone.invalidation_line, refs=[zone.zone_id]),
                _condition('BASE_STRUCTURE_NOT_BEARISH', session, 'INVALIDATION', structure, '!=', 'bearish',
                predicate=structure != 'bearish' if structure is not None else None, refs=[view.indicator_identity, f'structure:{session}'])]
            conditions.append(_condition('BASE_UPSIDE_EXIT', session, 'DIAGNOSTIC', bar.close, '>', event.upside_exit_line,
                predicate=bar.close > event.upside_exit_line, refs=[event.upper_boundary.source_id, f'bar:{session}']))
            position, pos_condition = _position(event, bar, policy, 'CURRENT_ELIGIBILITY')
            # Prior phase determines expiry, before recording this day's first touch.
            if required_conjunction(conditions) is False:
                reasons = ['BASE_SUPPORT_INVALIDATED' if bar.close < zone.invalidation_line else 'BASE_STRUCTURE_INVALIDATED']
                event = event.model_copy(update={'state': OpportunityStateName.INVALIDATED, 'base_validity': 'INVALIDATED'})
            elif bar.close > event.upside_exit_line:
                reasons = ['BASE_UPSIDE_EXIT']
                event = event.model_copy(update={'state': OpportunityStateName.EXPIRED, 'upside_exit_session': session})
            elif event.retest_session is None and session > event.first_touch_deadline:
                reasons = ['BASE_FIRST_TOUCH_WINDOW_EXPIRED']
                event = event.model_copy(update={'state': OpportunityStateName.EXPIRED})
            elif event.retest_session is None:
                if zone.tests:
                    test = zone.tests[0]
                    event = event.model_copy(update={'retest_session': session, 'live_test_id': test.test_id,
                        'confirmation_deadline': sessions[sessions.index(session)+input.opportunity_policy.confirmation_sessions],
                        'state': OpportunityStateName.CONFIRMING})
                    reasons = ['BASE_FIRST_LIVE_TOUCH']
            elif event.confirmation_session is None:
                if session > event.confirmation_deadline:
                    reasons = ['BASE_CONFIRMATION_WINDOW_EXPIRED']
                    event = event.model_copy(update={'state': OpportunityStateName.EXPIRED})
                elif session > event.retest_session:
                    confirm = confirmation_conditions(bar=bar, previous_bar=effective_bars.get(sessions[sessions.index(session)-1]),
                        history=list(effective_bars.values()), zone_upper=zone.upper, anchor_atr=zone.anchor_atr,
                        zone_id=zone.zone_id, test_id=event.live_test_id, support_fact=_fact(event, session), policy=input.opportunity_policy)
                    conditions += confirm + [pos_condition.model_copy(update={'role': 'CONFIRMATION'})]
                    if required_conjunction(conditions) is True:
                        i = sessions.index(session)
                        event = event.model_copy(update={'confirmation_session': session, 'state': OpportunityStateName.ENTRY_READY,
                            'entry_start': sessions[i+1], 'entry_end': sessions[i+input.opportunity_policy.entry_window_sessions]})
                        reasons = ['CONSTRUCTIVE_BASE_VERIFIED', 'CONFIRMATION_DAY_NOT_ENTRY_DAY']
            elif session > event.entry_end:
                reasons = ['BASE_ENTRY_WINDOW_EXPIRED']
                event = event.model_copy(update={'state': OpportunityStateName.EXPIRED})
            else:
                conditions += current_eligibility_conditions(bar=bar, zone_upper=zone.upper,
                    support_fact=_fact(event, session), policy=input.opportunity_policy) + [pos_condition]
                eligible = required_conjunction(conditions)
            conditions = _structure_conditions(conditions, [resolutions[session]])
            if event.state in TERMINAL:
                event = event.model_copy(update={'terminal_session': session, 'terminal_conditions': conditions})
                eligible = False
            elif required_conjunction(conditions) is None:
                eligible = None
            event = event.model_copy(update={'reason_codes': list(dict.fromkeys(event.reason_codes+reasons)),
                'base_validity': 'INVALIDATED' if event.state == OpportunityStateName.INVALIDATED else
                    'UNKNOWN' if structure is None else 'VALID'})
            events[-1] = event
        elif event:
            # Archival price evidence can continue; the first opportunity never restarts.
            zone, _ = update_fixed_support_zone(event.lower_zone, _support_bar(bar), sessions, input.support_policy, [])
            event = event.model_copy(update={'lower_zone': zone,
                'base_validity': 'INVALIDATED' if zone.broken_at or _structure(bar, resolutions) == 'bearish' else
                    'UNKNOWN' if _structure(bar, resolutions) is None else event.base_validity})
            events[-1] = event
            position, _ = _position(event, bar, policy, 'DIAGNOSTIC')
        may_form = event is None or (event.terminal_session and
            sessions.index(session)-sessions.index(event.terminal_session) >= policy.reformation_sessions)
        if may_form:
            candidate = _formation(input, session, sessions, bars, details, resolutions, policy_hash, source_hash)
            candidates.append(candidate)
            conditions += candidate.conditions
            detected = candidate.detected
            if detected is True:
                event = _event(candidate, event, input, sessions)
                events.append(event)
                reasons, eligible = ['BASE_FORMED', 'FORMATION_DAY_NOT_LIVE_TOUCH'], False
                position, _ = _position(event, bar, policy, 'DIAGNOSTIC')
            elif detected is None:
                eligible = None
        elif event and event.state in TERMINAL and event.terminal_session != session:
            reasons = ['BASE_REFORMATION_WINDOW_NOT_READY']
        timeline.append(BaseDay(session=session, structure_resolution=resolutions[session], **_snapshot(event), base_detected=detected,
            base_validity=event.base_validity if event else 'UNKNOWN' if detected is None else 'NOT_FORMED',
            opportunity_state=event.state if event else OpportunityStateName.NO_SETUP,
            eligible=eligible, base_position=position, conditions=conditions, reason_codes=reasons))
        evaluated, revision = session, revision+1
    last = timeline[-1] if timeline else None
    gaps = list(input_gaps)
    for c in candidates:
        gaps += c.missing_details
    for d in timeline:
        for c in d.conditions:
            if c.role != 'DIAGNOSTIC' and c.predicate_value is None:
                gaps.append(_gap(d.session, c.condition_id, c.role, c.source_refs,
                    c.reason_codes or ['CONDITION_INPUT_UNKNOWN'], ['current_assessment', 'coverage'] if d == last else ['coverage']))
    # Candidate prefix gaps remain dated coverage; only unresolved current gates block.
    current_gaps = [g for g in gaps if 'current_assessment' in g.affected_outputs]
    event = events[-1] if events else None
    eligible = requested_applicability(requested=requested, evidence=day, semantics=semantics,
        eligible=last.eligible if last else None, entry_end=event.entry_end if event else None)
    if semantics == 'CURRENT_EOD' and requested != day and eligible is None:
        for s in calendar.sessions_in_range(min(day, requested), max(day, requested)):
            s = str(s.date())
            if s != day:
                current_gaps.append(_gap(s, 'REQUESTED_SESSION_EVIDENCE_MISSING', 'INPUT',
                    [view.source.source_id, f'bar:{s}'], affected=['current_assessment']))
    state = BaseState(calculation_version=policy.calculation_version, symbol=ctx.symbol, analysis_start=start,
        evaluated_through=evaluated, state_revision=revision, input_prefix_sha256=prefix(evaluated),
        source_identity=source_hash, policy_identity=policy_hash, base_events=events, formation_candidates=candidates, timeline=timeline)
    expected = [s for s in sessions if start <= s <= day]
    coverage = OpportunityCoverage(expected_sessions=expected, actual_sessions=[s for s in expected if evaluated and s <= evaluated],
        missing_sessions=missing, analysis_start=start, evaluated_through=evaluated, indicator_seed_start=view.indicator_seed_start,
        indicator_identity=view.indicator_identity, legal_input_sha256=prefix(evaluated), source_identity=source_hash,
        support_identity=_hash([e.lower_zone.zone_id for e in events]), fields={'OHLC': CapabilityStatus.PARTIAL if missing else CapabilityStatus.COMPLETED},
        reason_codes=['DAILY_SESSION_GAPS'] if missing else [], display_sessions=[s for s in expected if s >= view.analysis_start],
        processed_sessions=[d.session for d in timeline])
    identity = _hash([state.model_dump(mode='json'), requested, semantics, policy.calculation_version])
    relations = [BaseBreakoutRelationship(base_id=e.base_id, session=e.upside_exit_session, handoff='NOT_EVALUATED',
        base_upper=e.upper, base_exit_line=e.upside_exit_line, reason_codes=['BREAKOUT_FAMILY_NOT_EVALUATED'])
        for e in events if e.upside_exit_session]
    return BaseResult(symbol=ctx.symbol, as_of=day, run_id=ctx.run_id, request_id=ctx.request_id,
        replay_of_result_id=input.replay_of_result_id,
        result_id=identity, status=CapabilityStatus.PARTIAL if eligible is None or missing else CapabilityStatus.COMPLETED,
        data_timestamp=view.source_timestamp, call_context=ctx, effective_policy=policy, support_policy=input.support_policy,
        opportunity_policy_values=opportunity_values, policy_sha256=policy_hash, price_basis=view.price_basis,
        calendar=input.calendar, requested_session=requested, request_time_semantics=semantics,
        base_detected=last.base_detected if last else None, base_validity=last.base_validity if last else 'UNKNOWN',
        opportunity_state=last.opportunity_state if last else OpportunityStateName.NO_SETUP, eligible_at_requested_time=eligible,
        formation_candidates=candidates, base_events=events, retrospective_evidence=[t for e in events for t in e.retrospective_tests],
        timeline=timeline, transitions=_transitions(ctx.symbol, timeline), relationships=relations,
        current_missing_details=current_gaps, coverage_missing_evidence=gaps, coverage=coverage, next_state=state,
        provenance=[view.source, *view.auxiliary_sources, *[details[s].source for s in sorted(details)]],
        reason_codes=list(dict.fromkeys([r for d in timeline for r in d.reason_codes]+[r for g in current_gaps for r in g.reason_codes])),
        explanation=base_explanation(event, last, requested, eligible))


def _structure_explanation(resolution):
    if resolution is None:
        return '结构证据未解析。'
    r = resolution
    overridden = '、'.join(r.overridden_sources) or '无'
    return (f'结构采用{r.selected_value if r.selected_value is not None else "未知"}，权威来源{r.selected_from}'
        f'（{"；".join(r.selected_source_refs)}）；原bar={r.bar_value}（{"；".join(r.bar_source_refs)}），'
        f'明细={r.detail.structure_state if r.detail else "未保存"}；'
        f'明细高/低比较={r.detail.high_comparison if r.detail else None}/{r.detail.low_comparison if r.detail else None}；'
        f'被覆盖来源：{overridden}；'
        f'说明：{"；".join(r.reason_codes)}。')


def base_explanation(event, last, requested, eligible):
    structure_text = _structure_explanation(last.structure_resolution if last else None)
    if event is None:
        return f'尚未形成平台；当前必要证据{"未知" if eligible is None else "未全部通过"}。逐日候选条件解释未成立原因；形态不代表公司质量或下单授权。{structure_text}'
    held = '、'.join(f'{t.test.touch_session}触及/{t.test.first_held_at}守住' for t in event.retrospective_tests if t.test.status == 'HELD')
    wait = ('平台机会已结束，等待结束后完整20日新形成窗口' if event.state in TERMINAL else
        f'等待首次实时触及，最晚{event.first_touch_deadline}' if not event.retest_session else
        f'等待确认，最晚{event.confirmation_deadline}' if not event.confirmation_session else
        f'已确认，当前窗口{event.entry_start}至{event.entry_end}')
    return (f'{event.formed_at}形成固定平台，上沿{event.upper:g}、下沿{event.lower:g}；历史守住：{held}，'
        f'这些回溯事实仅在形成日可知。最新位置{last.base_position if last else None}；{wait}。'
        f'收盘低于{event.lower_zone.invalidation_line:g}或结构确认下跌取消；收盘超过{event.upside_exit_line:g}向上离开。'
        f'请求交易日{requested}，资格{"未知" if eligible is None else "可评估" if eligible else "不可入场"}；不是下单授权。{structure_text}')


def base_markdown(result):
    labels = {'BASE_FORMATION_CONTIGUOUS_OHLC':'形成窗口连续完整', 'BASE_PRECEDING_CONTIGUOUS_OHLC':'前置窗口连续完整',
        'BASE_PRECEDING_BULLISH_EXISTS':'前置上涨基础', 'BASE_STRUCTURE_NOT_BEARISH':'当前结构未确认下跌',
        'BASE_FORMATION_ATR_VALID':'形成ATR有效', 'BASE_POSITIVE_WIDTH':'平台宽度为正',
        'BASE_WIDTH_ATR':'平台宽度/形成ATR', 'BASE_TR_CONTRACTION':'最近5日/此前15日TR中位数收缩',
        'BASE_INDEPENDENT_HELD_TESTS':'独立历史守住次数'}
    lines = ['平台形成逐条件对账（回溯事实仅在F可知，不是历史买点）：', '',
        '| 考察日 | 条件 | 实际值 | 比较 | 阈值 | 结果 |', '|---|---|---|---|---|---|']
    for candidate in result.formation_candidates:
        for c in candidate.conditions:
            if c.role == 'DIAGNOSTIC':
                continue
            value = '未知' if c.predicate_value is None else '通过' if c.predicate_value else '未通过'
            lines.append(f'| {candidate.session} | {labels.get(c.condition_id,c.condition_id)} ({c.condition_id}) | '
                f'{c.left_value} | {c.operator} | {c.right_value} | {value} |')
    for e in result.base_events:
        lines += ['', f'平台 {e.base_id}：{e.formed_at}形成；下沿{e.lower:g}，上沿{e.upper:g}；'
            f'首次实时触及{e.retest_session or "未发生"}，确认{e.confirmation_session or "未发生"}。',
            f'等待触及至{e.first_touch_deadline}，确认至{e.confirmation_deadline or "尚未开始"}；'
            f'收盘低于{e.lower_zone.invalidation_line:g}或确认下跌取消，超过{e.upside_exit_line:g}向上离开。',
            '| 历史触及 | 守住 | 离开 | 可知日 | 用于实时入场 |', '|---|---|---|---|---|']
        for t in e.retrospective_tests:
            lines.append(f'| {t.test.touch_session} | {t.test.first_held_at or "未守住"} | '
                f'{t.test.departure_session or "未离开"} | {t.known_at} | 否 |')
    resolutions = {r.session: r for c in result.formation_candidates for r in c.structure_resolutions}
    resolutions.update({d.session: d.structure_resolution for d in result.timeline if d.structure_resolution})
    lines += ['', '平台统一结构来源（各门槛通过 structure_resolution ID 引用同一记录）：', '',
        '| 日期 | 解析ID | 实际选择、原值与覆盖说明 |', '|---|---|---|']
    for session, resolution in sorted(resolutions.items()):
        lines.append(f'| {session} | {resolution.resolution_id} | {_structure_explanation(resolution)} |')
    return '\n'.join(lines)


def evaluate_base_opportunity(input):
    previous = next((r for r in input.prior_family_results if r.family == 'CONSTRUCTIVE_BASE'), None)
    result = detect_constructive_base(ConstructiveBaseInput(call_context=input.call_context,
        feature_view=input.feature_view, effective_policy=input.base_policy, opportunity_policy=input.effective_policy,
        structure_evidence=input.base_structure_evidence, calendar=input.calendar,
        replay_of_result_id=input.base_replay_of_result_id,
        prior_state=previous.base_result.next_state if previous and previous.base_result else None))
    episodes, days = [], []
    for e in result.base_events:
        if not e.retest_session:
            continue
        economic = _hash([result.symbol, e.lower_zone.zone_id, e.live_test_id])
        episodes.append(OpportunityEpisode(economic_episode_id=economic,
            opportunity_id=_hash([economic, result.policy_sha256]), family='CONSTRUCTIVE_BASE',
            setup_date=e.formed_at, touch_date=e.retest_session, confirmation_deadline=e.confirmation_deadline,
            confirmation_date=e.confirmation_session, entry_start=e.entry_start, entry_end=e.entry_end,
            terminal_date=e.terminal_session, state=e.state, zone_id=e.lower_zone.zone_id, test_id=e.live_test_id,
            zone_lower=e.lower_zone.lower, zone_upper=e.lower_zone.upper, anchor_atr=e.formation_atr,
            invalidation_line=e.lower_zone.invalidation_line, zone_available_at=e.formed_at,
            recent_high=e.upper, recent_high_session=e.upper_boundary.representative_session,
            parent_episode_id=e.parent_base_id, reason_codes=e.reason_codes))
    for d in result.timeline:
        economic = _economic(result.symbol, d)
        days.append(OpportunityDay(session=d.session, state=d.opportunity_state,
            capability_status=CapabilityStatus.PARTIAL if d.eligible is None else CapabilityStatus.COMPLETED,
            economic_episode_id=economic, opportunity_id=_hash([economic, result.policy_sha256]) if economic else None,
            setup_date=d.formed_at, touch_date=d.retest_session, confirmation_deadline=d.confirmation_deadline,
            confirmation_date=d.confirmation_session, entry_start=d.entry_start, entry_end=d.entry_end, eligible=d.eligible,
            support_zone_id=d.lower_zone_id, support_test_id=d.live_test_id, conditions=d.conditions, reason_codes=d.reason_codes))
    last = days[-1] if days else None
    event = result.base_events[-1] if result.base_events else None
    current = next((e for e in episodes if event and e.setup_date == event.formed_at), None)
    conditions = last.conditions if last else []
    rid = _hash(['entry-opportunity-v2.5-base', result.result_id])
    checkpoint = OpportunityStateCheckpoint(symbol=result.symbol, episodes=episodes,
        evaluated_through=result.next_state.evaluated_through, state_revision=result.next_state.state_revision,
        committed_result_id=rid, input_prefix_sha256=result.next_state.input_prefix_sha256,
        source_identity=result.next_state.source_identity, support_identity=result.coverage.support_identity,
        policy_sha256=result.policy_sha256, indicator_identity=input.feature_view.indicator_identity,
        price_basis=input.feature_view.price_basis, corporate_action_version=input.feature_view.corporate_action_version,
        analysis_start=result.next_state.analysis_start)
    return EntryOpportunity(symbol=result.symbol, version='1.4', calculation_version='entry-opportunity-v2.5',
        as_of=result.as_of, family='CONSTRUCTIVE_BASE', status=result.status, state=result.opportunity_state,
        last_known_state=result.opportunity_state, evaluated_through=result.next_state.evaluated_through,
        last_known_session=result.next_state.evaluated_through, entry_permitted_from=event.entry_start if event else None,
        entry_permitted_until=event.entry_end if event else None,
        confirmation_deadline_elapsed_at_requested_session=result.requested_session > event.confirmation_deadline if event and event.confirmation_deadline and not event.confirmation_session else False,
        entry_window_elapsed_at_requested_session=result.requested_session > event.entry_end if event and event.entry_end else False,
        eligible_at_requested_time=result.eligible_at_requested_time, requested_session=result.requested_session,
        request_time_semantics=result.request_time_semantics, economic_episode_id=current.economic_episode_id if current else None,
        opportunity_id=current.opportunity_id if current else None, result_id=rid,
        matched_families=['CONSTRUCTIVE_BASE'] if result.base_events else [], upstream_result_ids=[e.base_id for e in result.base_events],
        run_id=result.run_id, request_id=result.request_id, received_at=input.feature_view.received_at,
        effective_policy=input.effective_policy.model_copy(update={'family': 'CONSTRUCTIVE_BASE', 'policy_id': 'constructive-base-opportunity-v1'}),
        policy_sha256=result.policy_sha256, call_context=input.call_context, episodes=episodes, timeline=days,
        transitions=result.transitions, detections=[], current_conditions=conditions,
        supporting_evidence=[c.condition_id for c in conditions if c.predicate_value is True],
        opposing_evidence=[c.condition_id for c in conditions if c.predicate_value is False],
        missing_evidence=list(dict.fromkeys(g.condition_id for g in result.current_missing_details)),
        current_missing_details=result.current_missing_details, coverage_missing_evidence=result.coverage_missing_evidence,
        next_observation_conditions=[result.explanation], legacy_opinion=input.legacy_opinion, opinion_differences=[],
        coverage=result.coverage, next_state=checkpoint, provenance=result.provenance, explanation=result.explanation,
        active_families=['CONSTRUCTIVE_BASE'] if result.eligible_at_requested_time is True else [], base_result=result)


def base_breakout_relationships(results):
    base = next((r.base_result for r in results if r.base_result), None)
    breakout = next((r.breakout_result for r in results if r.breakout_result), None)
    if base is None:
        return []
    relations = []
    for relation in base.relationships:
        if breakout is None:
            relations.append(relation)
            continue
        event = next((e for e in breakout.events if e.breakout_session == relation.session), None)
        day = next((d for d in breakout.timeline if d.session == relation.session), None)
        if event:
            relations.append(relation.model_copy(update={'handoff': 'QUALIFIED', 'breakout_id': event.breakout_id,
                'breakout_resistance': event.resistance, 'breakout_line': event.breakout_line,
                'resistance_difference': event.resistance-relation.base_upper,
                'line_difference': event.breakout_line-relation.base_exit_line,
                'reason_codes': ['INDEPENDENT_BREAKOUT_QUALIFIED_AT_BASE_EXIT']}))
        else:
            reasons = ([c.condition_id for c in day.conditions if c.role == 'DISCOVERY' and c.predicate_value is not True]
                       if day else ['BREAKOUT_DAY_NOT_EVALUATED'])
            discovery = [c for c in day.conditions if c.role == 'DISCOVERY'] if day else []
            unknown = day is None or day.eligible is None and not discovery or discovery and required_conjunction(discovery) is None
            relations.append(relation.model_copy(update={'handoff': 'UNKNOWN' if unknown else 'NOT_QUALIFIED',
                'reason_codes': reasons or ['NO_NEW_INDEPENDENT_BREAKOUT_EVENT']}))
    return relations


def find_base_event(result, base_id):
    return next((e for e in result.base_events if e.base_id == base_id), None)


def find_base_day(result, session):
    return next((d for d in result.timeline if d.session == session), None)


def find_base_test(result, test_id):
    from pcs.trend.selection_models import BaseLiveTest
    for event in result.base_events:
        for test in event.retrospective_tests:
            if test.test.test_id == test_id:
                return test
        for test in event.lower_zone.tests:
            if test.test_id == test_id:
                return BaseLiveTest(known_at=test.touch_session, boundary_id=event.lower_zone.zone_id, test=test)
    return None
