from __future__ import annotations

from datetime import date, timedelta

from pcs.analysis_contracts import CallContext, SourceReference
from pcs.market_context import evaluate_market_context_opportunity
from pcs.pool.opportunities import evaluate_pool_opportunity_observation
from pcs.pool.opportunities import (load_opportunity_resume_evidence,
                                    load_opportunity_state,
                                    write_opportunity_artifacts)
from pcs.trend.opportunity_engine import evaluate_entry_opportunity, replay_entry_opportunity
from pcs.trend.selection_models import (
    OpportunityFeatureBar, OpportunityFeatureView, OpportunityInput,
    OpportunityStateName, OpportunitySupportFact,
)
from pcs.trend.setup_detectors import detect_healthy_pullback


def _sessions(count=42):
    out, day = [], date(2026, 1, 5)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _input(*, through=31, confirm_offset=2, missing=None, atr=2.0,
           zero_range=False, volume=True, received="2026-02-01T00:00:00Z",
           run="run-a", correction=0.0, prior=None, overdistance=None,
           bearish_on=None, break_on=None):
    sessions = _sessions()
    touch_i = 25
    bars = []
    for i, session in enumerate(sessions[:through+1]):
        close, low, high, op = 104.0, 103.0, 105.0, 104.0
        if i < 20:
            high = 110.0 if i == 10 else 106.0
        if i == touch_i:
            close, low, high, op = 100.0, 99.0, 101.0, 100.0
        elif i > touch_i:
            close, low, high, op = 100.0, 99.0, 102.0, 100.0
        if i == touch_i + confirm_offset:
            close, low, high, op = 102.0, 99.0, 103.0, 100.0
        if overdistance and i in overdistance:
            close, low, high, op = overdistance[i], 100.0, overdistance[i]+0.5, 101.0
        if zero_range and i == touch_i+1:
            close = low = high = op = 101.0
        if i == 3:
            close += correction
        bars.append(OpportunityFeatureBar(session=session, open=op, high=high,
            low=low, close=close, volume=100.0 if volume or i != touch_i+confirm_offset else None,
            sma20=100.0, sma50=99.0, sma200=95.0, ema200=95.5,
            atr14=atr if i != touch_i+1 or atr > 0 else 0.0,
            rsi14=55.0, structure_state="bearish" if bearish_on == i else "bullish",
            trend_health="healthy", trend_health_source="TEST",
            short_term_phase="HEALTHY_PULLBACK", short_term_phase_source="TEST"))
    if missing is not None:
        bars = [b for b in bars if b.session.isoformat() != sessions[missing]]
    source = SourceReference(source_id="test:daily", source_kind="TEST",
        schema_version="1", sha256="sha256:daily", record_identity="generation-1",
        validated=True)
    view = OpportunityFeatureView(symbol="TEST", bars=bars,
        expected_sessions=sessions, analysis_start=sessions[20],
        indicator_seed_start=sessions[0], indicator_identity="sha256:indicators",
        source=source, price_basis="split_adjusted", corporate_action_version="v1",
        input_kind="TEST", received_at=received)
    facts = []
    for i in range(touch_i, through+1):
        status = "HELD" if i >= touch_i+confirm_offset else "IN_PROGRESS"
        facts.append(OpportunitySupportFact(session=sessions[i], support_result_id=f"support:{i}",
            zone_id="zone-1", test_id="test-1", zone_lower=99.0, zone_upper=100.0,
            anchor_atr=2.0, invalidation_line=98.3, zone_available_at=sessions[touch_i-1],
            touch_session=sessions[touch_i], test_status=status,
            first_held_at=sessions[touch_i+confirm_offset] if status == "HELD" else None,
            broken_at=sessions[i] if break_on == i else None,
            zone_state="BROKEN" if break_on == i else ("SINGLE_HELD_TEST" if status == "HELD" else "TEST_IN_PROGRESS")))
    ids = {s: f"support:{i}" for i, s in enumerate(sessions[20:through+1], start=20)}
    ctx = CallContext(symbol="TEST", requested_as_of=sessions[through],
        effective_daily_session=sessions[through], mode="HISTORICAL",
        run_id=run, request_id=f"{run}:TEST", scope="ENTRY_OPPORTUNITY_V2_OBSERVATION")
    return OpportunityInput(call_context=ctx, feature_view=view, support_facts=facts,
        support_result_ids=ids, prior_state=prior,
        legacy_opinion={"production_action": "WAIT", "source": "fixture"})


def test_touch_s0_pending_s1_confirms_s2_and_window_starts_s3():
    result = evaluate_entry_opportunity(_input(through=30, confirm_offset=2))
    touch = result.timeline[5]
    s1, s2, s3, s5 = result.timeline[6], result.timeline[7], result.timeline[8], result.timeline[10]
    assert touch.state == OpportunityStateName.WATCH
    assert s1.state == OpportunityStateName.CONFIRMING
    assert s2.state == OpportunityStateName.ENTRY_READY and s2.eligible is False
    assert s2.confirmation_date == s2.session
    assert s2.entry_start == s3.session and s2.entry_end == s5.session
    assert s3.eligible is True and s5.eligible is True


def test_touch_plus_three_can_confirm_but_plus_four_cannot():
    legal = evaluate_entry_opportunity(_input(through=29, confirm_offset=3))
    confirmation_day = next(d for d in legal.timeline if d.session == _sessions()[28])
    assert confirmation_day.confirmation_date == confirmation_day.session
    late = evaluate_entry_opportunity(_input(through=29, confirm_offset=4))
    assert late.timeline[-1].state == OpportunityStateName.EXPIRED
    assert late.timeline[-1].confirmation_date is None


def test_invalidation_wins_over_favorable_conditions_same_day():
    result = evaluate_entry_opportunity(_input(through=27, confirm_offset=2, bearish_on=27))
    day = result.timeline[-1]
    assert day.state == OpportunityStateName.INVALIDATED
    assert any(c.condition_id == "CLOSE_ABOVE_FIXED_RECLAIM" and c.predicate_value for c in day.conditions)
    assert any(c.condition_id == "STRUCTURE_BEARISH_INVALIDATION" and c.predicate_value for c in day.conditions)


def test_gap_stops_at_last_known_day_and_eligibility_is_unknown():
    result = evaluate_entry_opportunity(_input(through=28, confirm_offset=2, missing=27))
    assert result.coverage.evaluated_through == _sessions()[26]
    assert result.eligible_at_requested_time is None
    assert result.status.value == "PARTIAL"
    assert result.timeline[-1].reason_codes[0] == "DAILY_BAR_MISSING"


def test_run_received_and_recovery_do_not_change_semantic_identity():
    a = evaluate_entry_opportunity(_input(through=28, received="2026-02-01T01:00:00Z", run="a"))
    b = evaluate_entry_opportunity(_input(through=28, received="2026-02-02T01:00:00Z", run="b"))
    resumed = evaluate_entry_opportunity(_input(through=28, run="c", prior=a.next_state))
    assert a.result_id == b.result_id == resumed.result_id
    assert a.episodes == b.episodes == resumed.episodes
    assert resumed.call_diagnostics == ["PRIOR_STATE_COMPATIBLE_REPLAY_VERIFIED"]


def test_overdistance_can_requalify_inside_frozen_window():
    values = {28: 104.0, 29: 102.0}
    result = evaluate_entry_opportunity(_input(through=29, overdistance=values))
    over, back = result.timeline[-2:]
    assert over.state == OpportunityStateName.ENTRY_READY and over.eligible is False
    assert back.state == OpportunityStateName.ENTRY_READY and back.eligible is True
    assert over.entry_start == back.entry_start and over.entry_end == back.entry_end
    assert over.confirmation_date == back.confirmation_date
    events = [t.event_type for t in result.transitions]
    assert "ENTRY_OVERDISTANCE" in events
    assert "ENTRY_REQUALIFIED_WITHIN_FIXED_WINDOW" in events


def test_fixed_zone_does_not_move_with_indicator_or_atr_changes():
    inp = _input(through=28)
    changed = inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={
        "bars": [b.model_copy(update={"sma20": 120.0, "sma50": 80.0, "atr14": 4.0})
                 if b.session.isoformat() == _sessions()[28] else b for b in inp.feature_view.bars]})})
    result = evaluate_entry_opportunity(changed)
    episode = result.episodes[0]
    assert (episode.zone_lower, episode.zone_upper, episode.anchor_atr, episode.invalidation_line) == (99.0, 100.0, 2.0, 98.3)


def test_invalid_range_and_missing_volume_are_unknown_not_confirmation():
    bad_range = evaluate_entry_opportunity(_input(through=26, zero_range=True, confirm_offset=1))
    condition = next(c for c in bad_range.timeline[-1].conditions if c.condition_id == "CLOSE_LOCATION")
    assert condition.predicate_value is None
    assert bad_range.timeline[-1].state == OpportunityStateName.CONFIRMING
    missing_volume = evaluate_entry_opportunity(_input(through=27, volume=False))
    condition = next(c for c in missing_volume.timeline[-1].conditions if c.condition_id == "RVOL20")
    assert condition.predicate_value is None
    assert missing_volume.timeline[-1].confirmation_date is None


def test_exact_five_percent_preserves_shallow_first_branch():
    inp = _input(through=25)
    bar = inp.feature_view.bars[25].model_copy(update={"close": 104.5, "sma20": 104.0, "sma50": 103.0})
    history = [b for b in inp.feature_view.bars[:25]] + [bar]
    # 110 high to 104.5 close is exactly 5%.
    detection = detect_healthy_pullback(bar=bar, history=history,
        support_facts=inp.support_facts, policy=inp.effective_policy)
    assert detection.legacy_pullback_state == "shallow_pullback"
    assert detection.detected is False


def test_history_correction_changes_result_but_original_is_immutable():
    original = evaluate_entry_opportunity(_input(through=28))
    old_json = original.model_dump_json()
    corrected = evaluate_entry_opportunity(_input(through=28, correction=0.25,
                                                   prior=original.next_state))
    assert corrected.result_id != original.result_id
    assert original.model_dump_json() == old_json
    assert corrected.call_diagnostics == ["PRIOR_STATE_INVALIDATED_FULL_REPLAY"]


def test_all_explicit_v2_entrypoints_use_same_core_and_legacy_is_untouched():
    inp = _input(through=28)
    direct = evaluate_entry_opportunity(inp)
    assert evaluate_pool_opportunity_observation(inp) == direct
    assert evaluate_market_context_opportunity(inp) == direct
    assert replay_entry_opportunity(inp) == direct
    assert direct.legacy_opinion["production_action"] == "WAIT"
    assert direct.opinion_differences[0]["reason_code"] == "V2_DOES_NOT_CHANGE_PRODUCTION_ACTION"


def test_confirmation_consumes_support_held_on_same_session():
    result = evaluate_entry_opportunity(_input(through=27, confirm_offset=2))
    assert result.timeline[-1].confirmation_date == _sessions()[27]
    held = next(c for c in result.timeline[-1].conditions
                if c.condition_id == "SUPPORT_HELD")
    assert held.predicate_value is True


def test_entry_window_expires_and_terminal_event_stays_terminal():
    result = evaluate_entry_opportunity(_input(through=32))
    assert result.timeline[-2].state == OpportunityStateName.EXPIRED
    assert result.timeline[-1].state == OpportunityStateName.EXPIRED
    assert result.timeline[-1].reason_codes == ["TERMINAL_EVENT_DOES_NOT_REVIVE"]


def test_future_append_does_not_change_historical_day_facts():
    shorter = evaluate_entry_opportunity(_input(through=28))
    longer = evaluate_entry_opportunity(_input(through=30))
    assert shorter.timeline == longer.timeline[:len(shorter.timeline)]
    assert shorter.episodes[0].economic_episode_id == longer.episodes[0].economic_episode_id
    assert shorter.episodes[0].opportunity_id == longer.episodes[0].opportunity_id


def test_cold_missing_support_snapshot_is_not_no_setup():
    inp = _input(through=24)
    result = evaluate_entry_opportunity(inp.model_copy(update={"support_result_ids": {}}))
    assert result.state is None
    assert result.last_known_state is None
    assert result.eligible_at_requested_time is None
    assert result.timeline[0].reason_codes[0] == "SUPPORT_SNAPSHOT_MISSING"


def test_artifacts_round_trip_models_hashes_and_unknown_csv(tmp_path):
    result = evaluate_entry_opportunity(_input(through=28, volume=False))
    root = write_opportunity_artifacts(tmp_path/"artifacts", [result], audit={"fixture": True})
    payload = __import__("json").loads((root/"entry_opportunities.json").read_text(encoding="utf-8"))
    from pcs.trend.selection_models import EntryOpportunity
    reread = EntryOpportunity.model_validate(payload[0])
    assert reread.result_id == result.result_id
    assert load_opportunity_state(root, "TEST") == result.next_state
    resume = load_opportunity_resume_evidence(root, "TEST")
    assert resume["prior_state"] == result.next_state
    assert resume["prior_timeline"] == result.timeline
    assert "entry_opportunities.ai.json" in __import__("json").loads(
        (root/"artifact_manifest.json").read_text(encoding="utf-8"))["sha256"]
    csv_text = (root/"entry_opportunities.csv").read_text(encoding="utf-8")
    assert ",," in csv_text  # unknown is empty, not false


def test_invalid_atr_and_zero_volume_denominator_are_unknown():
    inp = _input(through=27)
    bad_atr_bars = [b.model_copy(update={"atr14": 0.0})
                    if b.session.isoformat() == _sessions()[27] else b
                    for b in inp.feature_view.bars]
    bad_atr = evaluate_entry_opportunity(inp.model_copy(update={
        "feature_view": inp.feature_view.model_copy(update={"bars": bad_atr_bars})}))
    distance = next(c for c in bad_atr.timeline[-1].conditions
                    if c.condition_id == "DISTANCE_FROM_FIXED_ZONE")
    assert distance.predicate_value is None
    assert bad_atr.timeline[-1].confirmation_date is None

    zero_volume_bars = [b.model_copy(update={"volume": 0.0})
                        if 7 <= i < 27 else b
                        for i, b in enumerate(inp.feature_view.bars)]
    zero_volume = evaluate_entry_opportunity(inp.model_copy(update={
        "feature_view": inp.feature_view.model_copy(update={"bars": zero_volume_bars})}))
    rvol = next(c for c in zero_volume.timeline[-1].conditions if c.condition_id == "RVOL20")
    assert rvol.predicate_value is None
    assert rvol.reason_codes == ["RVOL_DENOMINATOR_NONPOSITIVE"]


def test_shorter_saved_prefix_then_continue_matches_cold_batch():
    partial = evaluate_entry_opportunity(_input(through=26))
    cold = evaluate_entry_opportunity(_input(through=30))
    resumed = evaluate_entry_opportunity(_input(through=30, prior=partial.next_state))
    assert resumed.result_id == cold.result_id
    assert resumed.episodes == cold.episodes
    assert resumed.timeline == cold.timeline
    assert resumed.transitions == cold.transitions
    assert resumed.call_diagnostics == ["PRIOR_STATE_COMPATIBLE_REPLAY_VERIFIED"]


def test_policy_or_zone_version_does_not_create_new_economic_event():
    base_input = _input(through=28)
    base = evaluate_entry_opportunity(base_input)
    policy_changed = evaluate_entry_opportunity(base_input.model_copy(update={
        "effective_policy": base_input.effective_policy.model_copy(update={
            "maximum_entry_distance_atr": 2.0, "parameter_source": "REQUEST"})}))
    facts = [f.model_copy(update={"zone_id": "zone-v2", "support_result_id": f"v2:{f.support_result_id}"})
             for f in base_input.support_facts]
    ids = {s: f"v2:{rid}" for s, rid in base_input.support_result_ids.items()}
    zone_changed = evaluate_entry_opportunity(base_input.model_copy(update={
        "support_facts": facts, "support_result_ids": ids}))
    assert base.episodes[0].economic_episode_id == policy_changed.episodes[0].economic_episode_id
    assert base.episodes[0].economic_episode_id == zone_changed.episodes[0].economic_episode_id
    assert base.episodes[0].opportunity_id != policy_changed.episodes[0].opportunity_id
    assert base.episodes[0].opportunity_id != zone_changed.episodes[0].opportunity_id


def test_false_invalidation_is_supporting_not_opposing_evidence():
    result = evaluate_entry_opportunity(_input(through=28))
    assert "CLOSE_BELOW_FIXED_INVALIDATION" in result.supporting_evidence
    assert "SUPPORT_ZONE_BROKEN" in result.supporting_evidence
    assert "CLOSE_BELOW_FIXED_INVALIDATION" not in result.opposing_evidence


def test_gap_keeps_market_state_unknown_but_reports_calendar_deadline_elapsed():
    result = evaluate_entry_opportunity(_input(through=30, missing=26))
    assert result.state == OpportunityStateName.WATCH
    assert result.eligible_at_requested_time is None
    assert result.confirmation_deadline_elapsed_at_requested_session is True
    assert result.entry_window_elapsed_at_requested_session is False
    assert "DAILY_BAR_MISSING" in result.coverage.reason_codes
    assert result.next_state.state_revision == 6  # analysis day 0..touch day only
    assert result.next_state.evaluated_through == _sessions()[25]


def test_bound_support_fact_can_carry_full_queryable_source():
    from pcs.trend.selection_models import SupportSourceAnchor
    inp = _input(through=25)
    source = SupportSourceAnchor(source_id="SMA20:2026-02-06",
        source_type="SMA20", price=100.0, observed_at="2026-02-06",
        available_at="2026-02-06")
    facts = [f.model_copy(update={"sources": [source], "source_ids": [source.source_id]})
             for f in inp.support_facts]
    result = evaluate_entry_opportunity(inp.model_copy(update={"support_facts": facts}))
    selected = next(d.selected_support for d in result.detections if d.detected)
    assert selected.sources == [source]
    assert selected.source_ids == [source.source_id]


def _replace_bars(inp, **changes):
    return inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={
        "bars": [b.model_copy(update=changes) for b in inp.feature_view.bars]})})


def _resume_details(inp, prior):
    return inp.model_copy(update={"prior_state": prior.next_state,
        "prior_timeline": prior.timeline, "prior_transitions": prior.transitions,
        "prior_detections": prior.detections})


def test_repaired_gap_resume_discards_uncommitted_gap_row():
    prior = evaluate_entry_opportunity(_input(through=30, missing=27))
    resumed = evaluate_entry_opportunity(_resume_details(_input(through=30), prior))
    cold = evaluate_entry_opportunity(_input(through=30))
    assert len(resumed.timeline) == len({d.session for d in resumed.timeline})
    assert resumed.timeline == cold.timeline
    assert resumed.transitions == cold.transitions
    assert resumed.status == cold.status
    assert resumed.coverage.reason_codes == cold.coverage.reason_codes
    assert resumed.missing_evidence == cold.missing_evidence
    assert resumed.result_id == cold.result_id


def test_later_display_start_cannot_skip_intermediate_confirmation(monkeypatch):
    import pcs.trend.opportunity_state as engine
    prior = evaluate_entry_opportunity(_input(through=26))
    inp = _resume_details(_input(through=30), prior)
    inp = inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={
        "analysis_start": _sessions()[29]})})
    cold = evaluate_entry_opportunity(_input(through=30))
    calls = []
    original = engine.detect_healthy_pullback
    def record(**kwargs):
        calls.append(kwargs["bar"].session.isoformat())
        return original(**kwargs)
    monkeypatch.setattr(engine, "detect_healthy_pullback", record)
    resumed = evaluate_entry_opportunity(inp)
    assert calls == _sessions()[27:31]
    assert resumed.coverage.processed_sessions == calls
    assert resumed.coverage.display_sessions == _sessions()[29:31]
    assert resumed.episodes == cold.episodes
    assert resumed.timeline == cold.timeline
    assert resumed.result_id == cold.result_id


def test_same_session_checkpoint_without_details_never_loses_state():
    import pytest
    prior = evaluate_entry_opportunity(_input(through=28))
    inp = _input(through=28, prior=prior.next_state)
    inp = inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={
        "analysis_start": _sessions()[28]})})
    with pytest.raises(ValueError, match="OPPORTUNITY_PRIOR_DETAILS_REQUIRED"):
        evaluate_entry_opportunity(inp)
    resumed = evaluate_entry_opportunity(_resume_details(inp, prior))
    assert resumed.state == prior.state
    assert resumed.eligible_at_requested_time == prior.eligible_at_requested_time
    assert resumed.result_id == prior.result_id
    assert resumed.coverage.processed_sessions == []


def test_unknown_discovery_is_named_in_current_missing_summary():
    inp = _replace_bars(_input(through=25), trend_health=None)
    result = evaluate_entry_opportunity(inp)
    assert result.missing_evidence == ["TREND_HEALTH_QUALIFIED"]
    assert result.status.value == "PARTIAL"


def test_skipped_display_range_stops_at_missing_intermediate_day():
    prior = evaluate_entry_opportunity(_input(through=26))
    inp = _resume_details(_input(through=30, missing=27), prior)
    inp = inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={
        "analysis_start": _sessions()[29]})})
    resumed = evaluate_entry_opportunity(inp)
    assert resumed.status.value == "PARTIAL"
    assert resumed.evaluated_through == _sessions()[26]
    assert resumed.eligible_at_requested_time is None
    assert resumed.next_state.state_revision == prior.next_state.state_revision
    assert resumed.coverage.processed_sessions == [_sessions()[27]]
    assert "DAILY_BAR_MISSING" in resumed.missing_evidence


def test_real_historical_unknown_survives_gap_repair_without_generic_reason():
    def unknown_at_touch(inp):
        return inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={
            "bars": [b.model_copy(update={"trend_health": None}) if
                b.session.isoformat() == _sessions()[25] else b for b in inp.feature_view.bars]})})
    prior = evaluate_entry_opportunity(unknown_at_touch(_input(through=30, missing=27)))
    complete = unknown_at_touch(_input(through=30))
    resumed = evaluate_entry_opportunity(_resume_details(complete, prior))
    cold = evaluate_entry_opportunity(complete)
    assert resumed.status.value == "PARTIAL"
    assert resumed.coverage.reason_codes == ["TREND_HEALTH_UNKNOWN"]
    assert resumed.coverage_missing_evidence == cold.coverage_missing_evidence
    assert resumed.result_id == cold.result_id
    assert not resumed.missing_evidence  # Last day is conclusive NO_SETUP.


def test_gap_summaries_are_consistent_in_all_views(tmp_path):
    import csv
    import json
    result = evaluate_entry_opportunity(_replace_bars(_input(through=25), trend_health=None))
    root = write_opportunity_artifacts(tmp_path/"gaps", [result])
    ai = json.loads((root/"entry_opportunities.ai.json").read_text(encoding="utf-8"))[0]
    structured = json.loads((root/"entry_opportunities.json").read_text(encoding="utf-8"))[0]
    assert ai["current_missing_details"] == structured["current_missing_details"]
    assert ai["coverage_missing_evidence"] == structured["coverage_missing_evidence"]
    rows = list(csv.DictReader((root/"entry_opportunities.csv").read_text(encoding="utf-8").splitlines()))
    assert json.loads(rows[-1]["current_missing_evidence"]) == result.missing_evidence
    flat = [g for row in rows for g in json.loads(row["coverage_missing_evidence"])]
    assert flat == structured["coverage_missing_evidence"]
    markdown = (root/"entry_opportunities.zh-CN.md").read_text(encoding="utf-8")
    assert "当前缺项：TREND_HEALTH_QUALIFIED" in markdown
    assert _sessions()[25] in markdown


def test_false_discovery_unknown_is_coverage_only_and_rsi_excluded():
    inp = _replace_bars(_input(through=25), trend_health=None,
                        structure_state="neutral", rsi14=None)
    result = evaluate_entry_opportunity(inp)
    assert result.state == OpportunityStateName.NO_SETUP
    assert result.eligible_at_requested_time is False
    assert result.current_missing_details == []
    assert result.missing_evidence == []
    assert all(g.affected_outputs == ["coverage"] for g in result.coverage_missing_evidence)
    assert all(g.role != "DIAGNOSTIC" for g in result.coverage_missing_evidence)


def test_discovery_requires_bullish_structure_and_qualified_real_health():
    for changes in ({"structure_state": "neutral", "trend_health": "mixed"},
                    {"structure_state": "deteriorating", "trend_health": "weakening"},
                    {"structure_state": "bullish", "trend_health": "broken"}):
        result = evaluate_entry_opportunity(_replace_bars(_input(through=28), **changes))
        assert all(d.detected is not True for d in result.detections)
        assert result.state != OpportunityStateName.ENTRY_READY


def test_explicit_short_term_phase_blocks_otherwise_favorable_confirmation():
    inp = _input(through=27)
    bars = [b.model_copy(update={"short_term_phase": "FAILED_FOLLOW_THROUGH"})
            if b.session.isoformat() == _sessions()[27] else b
            for b in inp.feature_view.bars]
    result = evaluate_entry_opportunity(inp.model_copy(update={
        "feature_view": inp.feature_view.model_copy(update={"bars": bars})}))
    phase = next(c for c in result.timeline[-1].conditions
                 if c.condition_id == "SHORT_TERM_PHASE_NOT_BLOCKED_CONFIRMATION")
    assert phase.predicate_value is False
    assert result.timeline[-1].confirmation_date is None


def test_required_unknown_is_not_no_setup_but_optional_rsi_is_non_blocking():
    unknown = evaluate_entry_opportunity(_replace_bars(_input(through=25), trend_health=None))
    assert unknown.state is None and unknown.eligible_at_requested_time is None
    assert unknown.status.value == "PARTIAL"
    inp = _input(through=27)
    bars = [b.model_copy(update={"rsi14": None})
            if b.session.isoformat() == _sessions()[27] else b
            for b in inp.feature_view.bars]
    optional = evaluate_entry_opportunity(inp.model_copy(update={
        "feature_view": inp.feature_view.model_copy(update={"bars": bars})}))
    assert optional.timeline[-1].confirmation_date == _sessions()[27]
    assert optional.timeline[-1].capability_status.value == "COMPLETED"

    explicit = evaluate_entry_opportunity(_input(through=24))
    assert explicit.state == OpportunityStateName.NO_SETUP
    assert explicit.eligible_at_requested_time is False


def test_current_eod_uses_calendar_resolved_request_session():
    historical = evaluate_entry_opportunity(_input(through=30))
    inp = _input(through=30)
    request_day = _sessions()[32]
    current = evaluate_entry_opportunity(inp.model_copy(update={"call_context":
        inp.call_context.model_copy(update={"mode": "CURRENT_EOD",
            "requested_as_of": f"{request_day}T22:00:00Z"})}))
    assert historical.eligible_at_requested_time is True
    assert current.requested_session == request_day
    assert current.entry_window_elapsed_at_requested_session is True
    assert current.eligible_at_requested_time is False
    assert current.result_id != historical.result_id


def test_checkpoint_continuation_processes_only_new_sessions_and_can_match_cold():
    partial = evaluate_entry_opportunity(_input(through=27))
    full_input = _input(through=30, prior=partial.next_state)
    resumed_input = full_input.model_copy(update={
        "prior_timeline": partial.timeline,
        "prior_transitions": partial.transitions,
        "prior_detections": partial.detections})
    resumed = evaluate_entry_opportunity(resumed_input)
    cold = evaluate_entry_opportunity(_input(through=30))
    assert resumed.timeline == cold.timeline
    assert resumed.transitions == cold.transitions
    assert resumed.result_id == cold.result_id
    assert resumed.call_diagnostics == ["PRIOR_STATE_COMPATIBLE_INCREMENTAL"]

    sliding = full_input.model_copy(update={"feature_view":
        full_input.feature_view.model_copy(update={"analysis_start": _sessions()[28]})})
    advanced = evaluate_entry_opportunity(_resume_details(sliding, partial))
    assert advanced.episodes[0].economic_episode_id == partial.episodes[0].economic_episode_id
    assert advanced.coverage.processed_sessions == _sessions()[28:31]
    assert advanced.timeline == cold.timeline


def test_corrected_sliding_prefix_requires_full_replay_prefix():
    import pytest
    partial = evaluate_entry_opportunity(_input(through=27))
    inp = _input(through=30, prior=partial.next_state, correction=0.5)
    sliding = inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={
        "analysis_start": _sessions()[28]})})
    with pytest.raises(ValueError, match="OPPORTUNITY_REPLAY_PREFIX_REQUIRED"):
        evaluate_entry_opportunity(sliding)
