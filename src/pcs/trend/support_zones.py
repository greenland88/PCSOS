"""Deterministic, replayable support zones and support-test evidence.

The public core consumes a prepared feature view. It does no storage, provider,
scanner, option, benchmark, scoring, or trading work.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from pcs.analysis_contracts import CapabilityStatus
from pcs.trend.selection_models import (
    SupportFeatureBar, SupportHistoryRecord, SupportIntradayBreach, SupportSourceAnchor,
    SupportTestEvent, SupportZone, SupportZoneCoverage, SupportZoneInput, SupportZonePolicy,
    SupportZoneResult, SupportZoneSelection, SupportZoneState,
)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _source_identity(view) -> str:
    return _hash([view.source.source_kind, view.source.sha256, view.source.record_identity,
                  view.price_basis, view.corporate_action_version])


def _policy_identity(policy) -> str:
    return _hash(policy.model_dump(mode="json"))


def _bar_payload(bars):
    return [bar.model_dump(mode="json") for bar in bars]


def _prefix_hash(bars, through=None):
    selected = [b for b in bars if through is None or b.session.isoformat() <= through]
    return _hash(_bar_payload(selected))


def _finite_positive(value):
    return value is not None and math.isfinite(float(value)) and float(value) > 0


def _record(history, *, session, zone, event_type, retrospective=False, test_id=None,
            close=None, low=None, high=None, reasons=()):
    payload = [zone.zone_id, session, event_type, test_id, list(reasons)]
    item = SupportHistoryRecord(history_id="sha256:" + _hash(payload), session=session,
        zone_id=zone.zone_id, event_type=event_type, known_at=session,
        retrospective=retrospective, close=close, low=low, high=high,
        zone_lower=zone.lower, zone_upper=zone.upper, invalidation_line=zone.invalidation_line,
        anchor_atr=zone.anchor_atr, zone_state=_zone_state(zone),
        test_id=test_id, reason_codes=list(reasons))
    if item.history_id not in {x.history_id for x in history}:
        history.append(item)
    return item


def _zone_state(zone):
    if zone.broken_at:
        return "BROKEN"
    if any(t.status == "IN_PROGRESS" for t in zone.tests):
        return "TEST_IN_PROGRESS"
    held = sum(t.status == "HELD" for t in zone.tests)
    return "REPEATED_HELD_TESTS" if held >= 2 else "SINGLE_HELD_TEST" if held == 1 else "REFERENCE_ONLY"


def _evidence_grade(state):
    return {"REFERENCE_ONLY": "REFERENCE", "TEST_IN_PROGRESS": "IN_PROGRESS",
            "SINGLE_HELD_TEST": "SINGLE_HELD", "REPEATED_HELD_TESTS": "REPEATED_HELD",
            "BROKEN": "BROKEN"}[state]


def _candidate_groups(anchors, atr, policy):
    width = policy.zone_width_atr * atr
    ordered = sorted(anchors, key=lambda x: (x.price, x.source_id))
    groups = []
    for anchor in ordered:
        if not groups or anchor.price - groups[-1][0].price > width:
            groups.append([anchor])
        else:
            # Compare with the first price, not a moving representative: no chain merge.
            groups[-1].append(anchor)
    return groups


def _new_zone(symbol, anchors, atr, policy, view):
    lo, hi = min(a.price for a in anchors), max(a.price for a in anchors)
    center = (lo + hi) / 2
    half = policy.zone_width_atr * atr / 2
    formed = max(a.observed_at for a in anchors)
    available = max(a.available_at for a in anchors)
    semantic = [symbol, round(center, 12), round(atr, 12), formed, available,
                [a.model_dump(mode="json") for a in anchors], policy.calculation_version,
                view.price_basis, view.corporate_action_version]
    kinds = {a.source_type for a in anchors}
    zone_type = "CONFLUENCE" if len(kinds) > 1 else "SWING_LOW" if kinds == {"CONFIRMED_SWING_LOW"} else "MA_REFERENCE"
    return SupportZone(zone_id="sha256:" + _hash(semantic), symbol=symbol, zone_type=zone_type,
        lower=center-half, upper=center+half, anchor_price=center, anchor_atr=atr,
        invalidation_line=center-half-policy.break_buffer_atr*atr,
        formed_at=formed, available_at=available, creation_sources=anchors,
        observed_source_ids=[a.source_id for a in anchors], price_basis=view.price_basis,
        corporate_action_version=view.corporate_action_version, policy_id=policy.policy_id,
        calculation_version=policy.calculation_version, state="REFERENCE_ONLY", evidence_grade="REFERENCE", tests=[],
        intraday_breaches=[], reason_codes=["SUPPORT_ZONE_FIXED_AT_FORMATION"])


def _intersects(bar, zone):
    return (bar.low is not None and bar.high is not None and
            math.isfinite(bar.low) and math.isfinite(bar.high) and
            bar.low <= zone.upper and bar.high >= zone.lower)


def _deadline(expected, touch, count):
    index = expected.index(touch)
    return expected[min(index + count, len(expected)-1)]


def _update_zone(zone, bar, expected, policy, history, changes):
    session = bar.session.isoformat()
    if session <= zone.available_at or zone.broken_at:
        return zone
    tests = list(zone.tests)
    breaches = list(zone.intraday_breaches)
    active_index = next((i for i, t in enumerate(tests) if t.status == "IN_PROGRESS"), None)

    if bar.low is not None and bar.low < zone.invalidation_line:
        breach = SupportIntradayBreach(session=session, low=bar.low,
            invalidation_line=zone.invalidation_line,
            penetration_atr=(zone.invalidation_line-bar.low)/zone.anchor_atr)
        if breach not in breaches:
            breaches.append(breach)
            changes.append(_record(history, session=session, zone=zone,
                event_type="INTRADAY_PENETRATION", low=bar.low, high=bar.high, close=bar.close,
                reasons=["INTRADAY_ONLY_UNLESS_CLOSE_BREAKS_FIXED_LINE"]))
    if bar.close is not None and bar.close < zone.invalidation_line:
        if active_index is not None:
            old = tests[active_index]
            tests[active_index] = old.model_copy(update={"cumulative_low": min(old.cumulative_low, bar.low or old.cumulative_low),
                "ended_at": session, "status": "UNCONFIRMED_TEST",
                "penetration_atr": max(old.penetration_atr, (zone.lower-(bar.low or zone.lower))/zone.anchor_atr),
                "reason_codes": list(dict.fromkeys(old.reason_codes+["ZONE_BROKEN_DURING_TEST"]))})
        broken = zone.model_copy(update={"tests": tests, "intraday_breaches": breaches,
            "broken_at": session, "broken_close": bar.close, "state": "BROKEN", "evidence_grade": "BROKEN",
            "reason_codes": list(dict.fromkeys(zone.reason_codes+["CLOSE_BELOW_FIXED_INVALIDATION_LINE"]))})
        changes.append(_record(history, session=session, zone=broken, event_type="BROKEN",
            low=bar.low, high=bar.high, close=bar.close, reasons=["CLOSE_BELOW_FIXED_INVALIDATION_LINE"]))
        return broken

    if active_index is not None:
        test = tests[active_index]
        cumulative = min(test.cumulative_low, bar.low if bar.low is not None else test.cumulative_low)
        penetration = max(test.penetration_atr, max(0, zone.lower-cumulative)/zone.anchor_atr)
        touch_i, current_i = expected.index(test.touch_session), expected.index(session)
        elapsed = current_i-touch_i
        held = (elapsed >= 1 and elapsed <= policy.confirmation_sessions and
                bar.close is not None and bar.close >= zone.upper and
                bar.close-cumulative >= policy.held_rebound_atr*zone.anchor_atr)
        if held:
            test = test.model_copy(update={"cumulative_low": cumulative, "status": "HELD",
                "first_held_at": session, "ended_at": session,
                "rebound_atr": (bar.close-cumulative)/zone.anchor_atr,
                "penetration_atr": penetration,
                "reason_codes": list(dict.fromkeys(test.reason_codes+["CLOSE_RECLAIMED_ZONE_UPPER", "FROZEN_ATR_REBOUND_SUFFICIENT"]))})
            tests[active_index] = test
            changed = zone.model_copy(update={"tests": tests, "intraday_breaches": breaches})
            changes.append(_record(history, session=session, zone=changed, event_type="TEST_HELD",
                test_id=test.test_id, low=bar.low, high=bar.high, close=bar.close,
                reasons=["FIRST_HELD_CONFIRMATION"]))
        elif elapsed > policy.confirmation_sessions:
            test = test.model_copy(update={"cumulative_low": cumulative, "status": "UNCONFIRMED_TEST",
                "ended_at": session, "penetration_atr": penetration,
                "reason_codes": list(dict.fromkeys(test.reason_codes+["CONFIRMATION_WINDOW_EXPIRED", "NOT_A_BREAK_WITHOUT_CLOSE_BELOW_INVALIDATION"]))})
            tests[active_index] = test
            changed = zone.model_copy(update={"tests": tests, "intraday_breaches": breaches})
            changes.append(_record(history, session=session, zone=changed, event_type="TEST_UNCONFIRMED",
                test_id=test.test_id, low=bar.low, high=bar.high, close=bar.close,
                reasons=["CONFIRMATION_WINDOW_EXPIRED"]))
        else:
            tests[active_index] = test.model_copy(update={"cumulative_low": cumulative,
                "penetration_atr": penetration})
            changed = zone.model_copy(update={"tests": tests, "intraday_breaches": breaches})
            _record(history, session=session, zone=changed, event_type="TEST_UPDATED",
                test_id=test.test_id, low=bar.low, high=bar.high, close=bar.close)
        zone = changed
    else:
        if tests and tests[-1].departure_session is None:
            last = tests[-1]
            if bar.close is not None and bar.close >= zone.upper + policy.retest_departure_atr*zone.anchor_atr:
                tests[-1] = last.model_copy(update={"departure_session": session,
                    "reason_codes": list(dict.fromkeys(last.reason_codes+["RETEST_DEPARTURE_CONFIRMED"]))})
                zone = zone.model_copy(update={"tests": tests, "intraday_breaches": breaches})
                changes.append(_record(history, session=session, zone=zone, event_type="DEPARTED",
                    test_id=last.test_id, low=bar.low, high=bar.high, close=bar.close))
        elif _intersects(bar, zone):
            test_id = "sha256:" + _hash([zone.zone_id, session])
            test = SupportTestEvent(test_id=test_id, touch_session=session,
                touch_low=bar.low, touch_high=bar.high, cumulative_low=bar.low,
                confirmation_deadline=_deadline(expected, session, policy.confirmation_sessions),
                status="IN_PROGRESS", penetration_atr=max(0, zone.lower-bar.low)/zone.anchor_atr,
                reason_codes=["ZONE_INTERSECTION_AFTER_AVAILABLE_AT", "TOUCH_DAY_NOT_CONFIRMABLE"])
            tests.append(test)
            zone = zone.model_copy(update={"tests": tests, "intraday_breaches": breaches})
            changes.append(_record(history, session=session, zone=zone, event_type="TEST_STARTED",
                test_id=test_id, low=bar.low, high=bar.high, close=bar.close,
                reasons=["TOUCH_DAY_NOT_CONFIRMABLE"]))
    state = _zone_state(zone)
    return zone.model_copy(update={"state": state, "evidence_grade": _evidence_grade(state)})


def _anchors_for_session(view, bar):
    session = bar.session.isoformat()
    anchors = []
    for name in ("sma20", "sma50"):
        price = getattr(bar, name)
        if _finite_positive(price):
            anchors.append(SupportSourceAnchor(source_id=f"{name.upper()}:{session}",
                source_type=name.upper(), price=price, observed_at=session, available_at=session))
    for swing in view.confirmed_swings:
        if swing.swing_type == "low" and swing.confirmed_at == session:
            anchors.append(SupportSourceAnchor(source_id=swing.source_id,
                source_type="CONFIRMED_SWING_LOW", price=swing.price,
                observed_at=swing.pivot_date, pivot_date=swing.pivot_date,
                available_at=swing.confirmed_at, retrospective=True))
    return anchors


def _select(zones, close, bound_zone_id):
    live = [z for z in zones if z.state != "BROKEN"]
    below = [z for z in live if close is not None and z.anchor_price <= close]
    recent = min(below, key=lambda z: (close-z.anchor_price, -int(z.available_at.replace("-", "")), z.zone_id), default=None)
    key = min(below, key=lambda z: (-sum(t.status == "HELD" for t in z.tests),
              -int(z.available_at.replace("-", "")), close-z.anchor_price, z.zone_id), default=None)
    bound = (next((z for z in zones if z.zone_id == bound_zone_id), None) if bound_zone_id else
             next((z for z in zones if z.bound), None))
    selections = [SupportZoneSelection(role="RECENT_OBSERVED_SUPPORT", zone_id=recent.zone_id if recent else None,
        status="SELECTED" if recent else "UNAVAILABLE", reason_codes=["NEAREST_NONBROKEN_ZONE_AT_OR_BELOW_CLOSE"] if recent else ["NO_LIVE_ZONE_AT_OR_BELOW_CLOSE"]),
        SupportZoneSelection(role="CANDIDATE_KEY_SUPPORT", zone_id=key.zone_id if key else None,
        status="SELECTED" if key else "UNAVAILABLE", reason_codes=["HELD_COUNT_THEN_RECENCY_THEN_DISTANCE"] if key else ["NO_ELIGIBLE_KEY_SUPPORT"]),
        SupportZoneSelection(role="BOUND_SUPPORT", zone_id=bound.zone_id if bound else None,
        status="SELECTED" if bound else "UNAVAILABLE" if bound_zone_id else "UNBOUND",
        reason_codes=["EXPLICIT_BOUND_ZONE"] if bound else ["BOUND_ZONE_NOT_FOUND"] if bound_zone_id else ["NO_OPPORTUNITY_BINDING_INPUT"])]
    chosen = {s.zone_id for s in selections if s.zone_id}
    unselected = [{"zone_id": z.zone_id, "reason_codes": ["ZONE_BROKEN"] if z.state == "BROKEN" else
                   ["NOT_SELECTED_BY_CURRENT_ROLE_POLICY"]} for z in zones if z.zone_id not in chosen]
    return selections, unselected


def evaluate_support_zones(input: SupportZoneInput) -> SupportZoneResult:
    """Replay or advance fixed zones using only completed typed daily facts."""
    ctx, view, policy = input.call_context, input.feature_view, input.effective_policy
    if view.symbol != ctx.symbol:
        raise ValueError("SUPPORT_SYMBOL_MISMATCH")
    if ctx.effective_daily_session is None:
        raise ValueError("SUPPORT_EFFECTIVE_SESSION_REQUIRED")
    if view.input_kind == "VERIFIED_CANONICAL" and (not view.source.validated or not view.source.sha256 or not view.source.record_identity):
        raise ValueError("SUPPORT_UNVERIFIED_SOURCE")
    if view.input_kind == "TEST" and view.source.source_kind != "TEST":
        raise ValueError("SUPPORT_TEST_IDENTITY_REQUIRED")
    if not view.price_basis or not view.corporate_action_version or not view.indicator_identity:
        raise ValueError("SUPPORT_INPUT_IDENTITY_MISSING")
    expected_all = [s for s in view.expected_sessions if s >= view.analysis_start]
    expected = [s for s in expected_all if s <= ctx.effective_daily_session]
    if expected_all != sorted(set(expected_all)) or not expected or ctx.effective_daily_session not in expected_all:
        raise ValueError("SUPPORT_EXPECTED_SESSIONS_INVALID")
    bars = [b for b in view.bars if view.analysis_start <= b.session.isoformat() <= ctx.effective_daily_session]
    dates = [b.session.isoformat() for b in bars]
    if dates != sorted(set(dates)) or any(d not in expected for d in dates):
        raise ValueError("SUPPORT_DATES_NOT_UNIQUE_SORTED")
    for bar in bars:
        values = (bar.open, bar.high, bar.low, bar.close, bar.atr14)
        present = [v for v in values if v is not None]
        if any(not math.isfinite(float(v)) or float(v) <= 0 for v in present):
            raise ValueError("SUPPORT_BAR_VALUE_INVALID")
        if all(v is not None for v in (bar.open, bar.high, bar.low, bar.close)) and (
                bar.high < bar.low or bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close)):
            raise ValueError("SUPPORT_OHLC_RELATIONSHIP_INVALID")
    for swing in view.confirmed_swings:
        if not swing.source_id or not _finite_positive(swing.price) or swing.confirmed_at < swing.pivot_date:
            raise ValueError("SUPPORT_SWING_EVIDENCE_INVALID")
        if swing.pivot_date in expected_all and swing.confirmed_at in expected_all:
            if expected_all.index(swing.confirmed_at)-expected_all.index(swing.pivot_date) < policy.pivot_right_bars:
                raise ValueError("SUPPORT_SWING_CONFIRMATION_TOO_EARLY")
    source_identity, policy_sha = _source_identity(view), _policy_identity(policy)
    prior = input.prior_state
    compatible = False
    if prior is not None:
        through_bars = [b for b in bars if prior.evaluated_through and b.session.isoformat() <= prior.evaluated_through]
        compatible = (prior.symbol == ctx.symbol and prior.source_identity == source_identity and
            prior.price_basis == view.price_basis and prior.corporate_action_version == view.corporate_action_version and
            prior.policy_sha256 == policy_sha and prior.indicator_identity == view.indicator_identity and
            prior.input_prefix_sha256 == _prefix_hash(through_bars) and
            (prior.evaluated_through is None or prior.evaluated_through in expected))
    zones = deepcopy(prior.zones) if compatible else []
    history = deepcopy(prior.support_history) if compatible else []
    start_after = prior.evaluated_through if compatible else None
    state_revision = prior.state_revision if compatible else 0
    reason_codes = [] if prior is None or compatible else ["PRIOR_STATE_INVALIDATED_REPLAYED"]
    changes = []
    actual_by_date = {b.session.isoformat(): b for b in bars}
    process_expected = [s for s in expected if start_after is None or s > start_after]
    evaluated = start_after
    started = start_after is not None
    for session in process_expected:
        bar = actual_by_date.get(session)
        if bar is None:
            if started:
                reason_codes.append("INTERMEDIATE_DAILY_SESSION_MISSING")
                break
            reason_codes.append("LEADING_HISTORY_MISSING")
            continue
        started = True
        required = (bar.high, bar.low, bar.close, bar.atr14)
        if not all(_finite_positive(v) for v in required):
            reason_codes.append("SUPPORT_REQUIRED_BAR_FIELD_MISSING")
            break
        # Existing zones see the completed bar before any sources only knowable at its close.
        zones = [_update_zone(z, bar, expected_all, policy, history, changes) for z in zones]
        anchors = _anchors_for_session(view, bar)
        for group in _candidate_groups(anchors, bar.atr14, policy):
            match = next((z for z in zones if z.state != "BROKEN" and
                          all(z.lower <= a.price <= z.upper for a in group)), None)
            if match:
                ids = list(dict.fromkeys(match.observed_source_ids+[a.source_id for a in group]))
                replacement = match.model_copy(update={"observed_source_ids": ids})
                zones[zones.index(match)] = replacement
                _record(history, session=session, zone=replacement, event_type="SOURCE_RESONANCE",
                        reasons=["SOURCE_WITHIN_EXISTING_FIXED_ZONE"])
            else:
                zone = _new_zone(ctx.symbol, group, bar.atr14, policy, view)
                zones.append(zone)
                changes.append(_record(history, session=session, zone=zone, event_type="ZONE_FORMED",
                    reasons=["BOUNDED_NON_CHAIN_CLUSTER", "ZONE_KNOWN_AFTER_SESSION_CLOSE"]))
                for anchor in group:
                    pivot_bar = actual_by_date.get(anchor.pivot_date) if anchor.pivot_date else None
                    if anchor.retrospective:
                        changes.append(_record(history, session=session, zone=zone,
                            event_type="RETROSPECTIVE_INTERSECTION", retrospective=True,
                            low=pivot_bar.low if pivot_bar else None,
                            high=pivot_bar.high if pivot_bar else None,
                            close=pivot_bar.close if pivot_bar else None,
                            reasons=["PIVOT_BAR_PRECEDES_ZONE_KNOWN_AT", "NOT_A_SUPPORT_TEST"]))
        # This is the compact daily state ledger; transitions remain separately indexed.
        for zone in zones:
            _record(history, session=session, zone=zone, event_type="DAILY_STATE",
                close=bar.close, low=bar.low, high=bar.high,
                reasons=[f"ZONE_STATE_{zone.state}"])
        evaluated = session
        state_revision += 1
    if input.bound_zone_id:
        zones = [z.model_copy(update={"bound": z.zone_id == input.bound_zone_id or z.bound}) for z in zones]
    current_close = actual_by_date[evaluated].close if evaluated in actual_by_date else None
    selections, unselected = _select(zones, current_close, input.bound_zone_id)
    fields = {name: CapabilityStatus.COMPLETED if all(getattr(actual_by_date[s], name) is not None
              for s in actual_by_date) else CapabilityStatus.PARTIAL for name in
              ("open", "high", "low", "close", "sma20", "sma50", "atr14")}
    missing = [s for s in expected if s not in actual_by_date]
    if missing:
        reason_codes.append("SUPPORT_HISTORY_PARTIAL")
    reason_codes = list(dict.fromkeys(reason_codes))
    status = CapabilityStatus.PARTIAL if reason_codes else CapabilityStatus.COMPLETED
    input_hash = _prefix_hash(bars, evaluated)
    next_state = SupportZoneState(symbol=ctx.symbol, zones=zones, support_history=history,
        evaluated_through=evaluated, state_revision=state_revision, input_prefix_sha256=input_hash,
        source_identity=source_identity, price_basis=view.price_basis,
        corporate_action_version=view.corporate_action_version, policy_sha256=policy_sha,
        indicator_identity=view.indicator_identity)
    result = SupportZoneResult(symbol=ctx.symbol, as_of=ctx.effective_daily_session, status=status,
        data_timestamp=view.source_timestamp, received_at=view.received_at,
        run_id=ctx.run_id, request_id=ctx.request_id,
        result_id="pending", reason_codes=reason_codes, call_context=ctx, effective_policy=policy,
        policy_sha256=policy_sha, current_zones=[z for z in zones if z.state != "BROKEN"],
        archived_zones=[z for z in zones if z.state == "BROKEN"], support_history=history,
        state_changes=changes, selections=selections, unselected_zones=unselected,
        coverage=SupportZoneCoverage(expected_sessions=expected, actual_sessions=dates,
            missing_sessions=missing, analysis_start=view.analysis_start, evaluated_through=evaluated,
            indicator_seed_start=view.indicator_seed_start, indicator_identity=view.indicator_identity,
            legal_input_sha256=input_hash, source_identity=source_identity, fields=fields,
            reason_codes=reason_codes), next_state=next_state, provenance=[view.source],
        explanation="区域边界、建区ATR和失效线在形成时冻结。HELD仅记录区域可知后的独立测试，不构成交易许可。")
    semantic = result.model_dump(mode="json", exclude={"run_id", "request_id", "result_id", "received_at", "call_context", "provenance", "state_changes"})
    semantic["sources"] = [(view.source.source_kind, view.source.sha256, view.source.record_identity)]
    return result.model_copy(update={"result_id": "sha256:" + _hash(semantic)})


__all__ = ["SupportZoneInput", "SupportZoneResult", "evaluate_support_zones"]
