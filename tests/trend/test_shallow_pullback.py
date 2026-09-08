from __future__ import annotations

import pytest
import exchange_calendars as xc

from pcs.analysis_contracts import CallContext, SourceReference
from pcs.trend.selection_models import (OpportunityFeatureBar, OpportunityFeatureView,
    OpportunityInput, OpportunitySupportFact, ShallowPullbackInput)
from pcs.trend.setup_detectors import detect_shallow_pullback
from pcs.trend.opportunity_engine import evaluate_entry_opportunity


def sample(through=28, low=98.0, changes=None, missing=None):
    days = [str(s.date()) for s in xc.get_calendar("XNYS").sessions_in_range("2026-01-02", "2026-03-31")]
    bars = []
    for i, s in enumerate(days[:through+1]):
        o,h,l,c = 99.2,99.8,99.0,99.2
        if i == 23:
            o,h,l,c = 99.6,100.,99.5,99.8
        if i == 25:
            o,h,l,c = 99.,max(99.,low+0.5),low,max(98.5,low+0.2)
        if i == 26:
            o,h,l,c = 98.5,99.5,98.,98.1
        if i == 27:
            o,h,l,c = 98.2,100.,98.,99.6
        if i >= 28:
            o,h,l,c = 99.5,100.,99.,99.6
        values=dict(session=s,open=o,high=h,low=l,close=c,volume=100.,sma20=98.2,
            sma50=98.,sma200=90.,ema200=90.,atr14=4.,rsi14=55.,structure_state="bullish",
            trend_health="healthy",trend_health_source="TEST:interpret_trend",
            short_term_phase="HEALTHY_PULLBACK",short_term_phase_source="TEST:phase")
        values.update((changes or {}).get(i, {}))
        if i != missing:
            bars.append(OpportunityFeatureBar(**values))
    view=OpportunityFeatureView(symbol="TEST",bars=bars,expected_sessions=days,
        analysis_start=days[25],indicator_seed_start=days[0],indicator_identity="TEST:indicators",
        source=SourceReference(source_id="TEST:daily",source_kind="TEST",sha256="TEST:sha",
            record_identity="TEST:generation",validated=True), price_basis="TEST:adjusted",
        corporate_action_version="TEST:v1",input_kind="TEST")
    facts=[OpportunitySupportFact(session=s,support_result_id=f"TEST:support:{s}",
        zone_id="TEST:zone",test_id="TEST:test",zone_lower=97.8,zone_upper=98.2,
        anchor_atr=2.,invalidation_line=97.1,zone_available_at=days[24],touch_session=days[25],
        test_status="HELD" if i>=27 else "IN_PROGRESS",first_held_at=days[27] if i>=27 else None,
        zone_state="SINGLE_HELD_TEST" if i>=27 else "TEST_IN_PROGRESS")
        for i,s in enumerate(days[:through+1]) if i>=25]
    ctx=CallContext(symbol="TEST",requested_as_of=days[through],effective_daily_session=days[through],
        mode="HISTORICAL",run_id="test",request_id="test")
    return OpportunityInput(call_context=ctx,feature_view=view,support_facts=facts,
        support_result_ids={f.session:f.support_result_id for f in facts},enabled_families=["SHALLOW_PULLBACK"])


def detect(inp, prior=None):
    return detect_shallow_pullback(ShallowPullbackInput(call_context=inp.call_context,
        feature_view=inp.feature_view,support_facts=inp.support_facts,prior_state=prior))


@pytest.mark.parametrize("low,depth,passed", [(99.,.25,True),(94.,1.5,True),(99.1,.225,False),(93.9,1.525,False)])
def test_inclusive_depth_bounds(low,depth,passed):
    r=detect(sample(25,low=low))
    assert r.next_state.depth_at_touch == pytest.approx(depth)
    assert r.detected is passed


def test_normal_touch_confirmation_and_frozen_window():
    r=evaluate_entry_opportunity(sample(30))
    assert [d.state.value for d in r.timeline[:3]] == ["WATCH","CONFIRMING","ENTRY_READY"]
    assert r.timeline[2].eligible is False
    assert r.timeline[3].eligible is True
    assert r.episodes[0].entry_start == r.timeline[3].session
    assert r.episodes[0].entry_end == r.timeline[5].session
    assert r.episodes[0].shallow_state.depth_at_touch == .5


def test_touch_atr_does_not_change_depth_denominator():
    r=detect(sample(25,changes={25:{"atr14":8.}}))
    assert r.next_state.depth_anchor_atr==4.
    assert r.next_state.depth_at_touch==.5


def test_pre_touch_deep_low_rejects_shallow_relabel():
    r=detect(sample(25,changes={24:{"low":92.8}}))
    assert r.next_state.current_depth_atr==pytest.approx(1.8)
    assert r.detected is False
    assert "SETUP_DEPTH_EXCEEDED" in r.reason_codes


def test_depth_qualification_ends_without_structure_or_close_break_and_never_revives():
    inp=sample(30,changes={28:{"low":92.8,"close":99.6}})
    r=evaluate_entry_opportunity(inp)
    e=r.episodes[0]
    assert e.state.value=="INVALIDATED"
    assert e.invalidation_scope=="SETUP_QUALIFICATION"
    assert e.shallow_state.first_depth_exceeded==inp.feature_view.expected_sessions[28]
    day=r.timeline[3]
    assert next(c for c in day.conditions if c.condition_id=="STRUCTURE_BEARISH_INVALIDATION").predicate_value is False
    assert r.timeline[-1].eligible is False


def test_same_bar_new_high_is_not_peak_and_tie_is_latest():
    r=detect(sample(25,changes={22:{"high":100.},25:{"high":101.}}))
    assert r.next_state.peak_price==100.
    assert r.next_state.peak_session==sample().feature_view.expected_sessions[23]
    assert r.same_bar_new_high_and_touch is True


@pytest.mark.parametrize("changes,missing", [({24:{"atr14":None}},None),({},10),({25:{"trend_health":None}},None)])
def test_required_missing_is_unknown(changes,missing):
    r=detect(sample(25,changes=changes,missing=missing))
    assert r.detected is None and r.status.value=="PARTIAL"


def test_bad_trends_and_no_touch_never_discover():
    for change in ({"structure_state":"neutral"},{"trend_health":"broken"},{"short_term_phase":"FAILED_FOLLOW_THROUGH"}):
        assert detect(sample(25,changes={25:change})).detected is False
    assert detect(sample(24)).detected is False


def test_missing_confirmation_volume_and_optional_rsi():
    bad=evaluate_entry_opportunity(sample(27,changes={27:{"volume":None}}))
    assert bad.episodes[0].confirmation_date is None
    assert "RVOL20" in bad.missing_evidence
    good=evaluate_entry_opportunity(sample(27,changes={27:{"rsi14":None}}))
    assert good.episodes[0].confirmation_date is not None


def test_family_switch_order_and_healthy_identity_are_independent(monkeypatch):
    inp=sample(28)
    both=evaluate_entry_opportunity(inp.model_copy(update={"enabled_families":["SHALLOW_PULLBACK","HEALTHY_PULLBACK"]}))
    healthy=evaluate_entry_opportunity(inp.model_copy(update={"enabled_families":["HEALTHY_PULLBACK"]}))
    assert both.family_results[0].result_id==healthy.result_id
    reverse=evaluate_entry_opportunity(inp.model_copy(update={"enabled_families":["HEALTHY_PULLBACK","SHALLOW_PULLBACK"]}))
    assert reverse.result_id==both.result_id
    import pcs.trend.opportunity_state as state
    monkeypatch.setattr(state,"detect_healthy_pullback",lambda **kwargs: pytest.fail("healthy detector called"))
    assert evaluate_entry_opportunity(inp).family=="SHALLOW_PULLBACK"


def test_resume_and_gap_repair_match_cold_including_frozen_anchors():
    for first in (sample(26),sample(30,missing=27)):
        prior=evaluate_entry_opportunity(first)
        resumed=evaluate_entry_opportunity(sample(30).model_copy(update={"prior_family_results":[prior]}))
        cold=evaluate_entry_opportunity(sample(30))
        assert resumed.episodes==cold.episodes
        assert resumed.timeline==cold.timeline
        assert [e.model_dump(exclude={"call_context"}) for e in resumed.shallow_pullback_timeline] == [
            e.model_dump(exclude={"call_context"}) for e in cold.shallow_pullback_timeline]
        assert resumed.result_id==cold.result_id


def test_future_rows_and_received_time_do_not_change_historical_result():
    inp=sample(27)
    future=sample(30)
    r=evaluate_entry_opportunity(inp)
    altered=inp.model_copy(update={"feature_view":inp.feature_view.model_copy(update={
        "bars":future.feature_view.bars,"received_at":"2030-01-01T00:00:00Z"})})
    other=evaluate_entry_opportunity(altered)
    assert other.result_id==r.result_id


def test_price_scaling_preserves_depth_and_lifecycle():
    inp=sample(30)
    bars=[b.model_copy(update={k:getattr(b,k)*10 for k in
        ("open","high","low","close","sma20","sma50","sma200","ema200","atr14")}) for b in inp.feature_view.bars]
    facts=[f.model_copy(update={k:getattr(f,k)*10 for k in
        ("zone_lower","zone_upper","anchor_atr","invalidation_line")}) for f in inp.support_facts]
    scaled=evaluate_entry_opportunity(inp.model_copy(update={"support_facts":facts,
        "feature_view":inp.feature_view.model_copy(update={"bars":bars})}))
    original=evaluate_entry_opportunity(inp)
    assert [d.state for d in scaled.timeline]==[d.state for d in original.timeline]
    assert scaled.episodes[0].shallow_state.current_depth_atr==pytest.approx(original.episodes[0].shallow_state.current_depth_atr)


def test_detector_same_day_restore_identity_and_corrected_prefix():
    inp = sample(25)
    first = detect(inp)
    restored = detect(inp, first.next_state)
    assert first == restored
    corrected = sample(25, changes={20: {"high": 100.5}})
    with pytest.raises(ValueError, match="PREFIX_REPLAY_REQUIRED"):
        detect(corrected, first.next_state)


def test_bundle_exports_and_saved_typed_inputs(tmp_path):
    import json
    from pcs.pool.opportunities import write_opportunity_artifacts, read_opportunity_bundle
    inp = sample(28).model_copy(update={"enabled_families": ["HEALTHY_PULLBACK", "SHALLOW_PULLBACK"]})
    result = evaluate_entry_opportunity(inp)
    write_opportunity_artifacts(tmp_path, [result], inputs=[inp])
    manifest, docs = read_opportunity_bundle(tmp_path)
    assert OpportunityInput.model_validate(docs["prepared_opportunity_inputs.json"][0]) == inp
    ai = json.loads((tmp_path/"entry_opportunities.ai.json").read_text())
    assert ai[0]["result_id"] == result.result_id
    assert len(ai[0]["family_results"]) == 2
    assert "SHALLOW_PULLBACK" in (tmp_path/"entry_opportunities.zh-CN.md").read_text(encoding="utf-8")


def test_price_only_opt_in_does_not_fabricate_volume():
    import pandas as pd
    from pcs.trend.indicators import calculate_base_indicators
    from pcs.trend.snapshot import build_trend_snapshot
    from pcs.trend.models import TrendIndicatorValidationError
    close = pd.Series(range(100, 360), dtype=float)
    frame = pd.DataFrame(dict(date=pd.date_range("2025-01-01", periods=260),
        open=close-.5, high=close+1, low=close-1, close=close, volume=100.))
    complete = calculate_base_indicators(frame)
    frame.loc[250:, "volume"] = float("nan")
    with pytest.raises(TrendIndicatorValidationError):
        calculate_base_indicators(frame)
    partial = calculate_base_indicators(frame, allow_missing_volume=True)
    pd.testing.assert_frame_equal(complete, partial)
    snap = build_trend_snapshot(frame, allow_missing_volume=True)
    assert snap.ma_structure.available
    assert snap.evidence_series[-1]["volume"] is None
    assert frame.volume.isna().sum() == 10


def test_overlapping_families_keep_healthy_after_shallow_depth_failure():
    inp = sample(28, low=94., changes={
        25: {"open": 95., "high": 95.5, "close": 94.8, "sma20": 94.5, "sma50": 94.},
        26: {"open": 94.8, "high": 96., "low": 94., "close": 95.8},
        27: {"open": 95.8, "high": 97., "low": 92.8, "close": 96.5},
        28: {"open": 96.5, "high": 97., "low": 96., "close": 96.8}})
    facts = [f.model_copy(update={"zone_lower": 94., "zone_upper": 94.7,
        "invalidation_line": 93.3, "test_status": "HELD",
        "first_held_at": inp.feature_view.expected_sessions[26]})
        for f in inp.support_facts]
    result = evaluate_entry_opportunity(inp.model_copy(update={"support_facts": facts,
        "enabled_families": ["HEALTHY_PULLBACK", "SHALLOW_PULLBACK"]}))
    healthy, shallow = result.family_results
    assert healthy.episodes and shallow.episodes
    assert healthy.episodes[0].economic_episode_id == shallow.episodes[0].economic_episode_id
    assert healthy.eligible_at_requested_time is True
    assert shallow.episodes[0].invalidation_scope == "SETUP_QUALIFICATION"
    assert result.active_families == ["HEALTHY_PULLBACK"]
    assert result.economic_events[0]["historical_families"] == ["HEALTHY_PULLBACK", "SHALLOW_PULLBACK"]
