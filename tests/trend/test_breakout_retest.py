from __future__ import annotations

import exchange_calendars as xc
import pytest

from pcs.analysis_contracts import CallContext, SourceReference
from pcs.trend.breakout_retest import detect_breakout_retest
from pcs.trend.opportunity_engine import evaluate_entry_opportunity
from pcs.trend.selection_models import (BreakoutRetestInput, OpportunityFeatureBar,
    OpportunityFeatureView, OpportunityInput)


def sample(through=32, *, b=25, retest=27, confirm=29, changes=None,
           missing=None, families=("BREAKOUT_RETEST",)):
    days = [str(s.date()) for s in xc.get_calendar("XNYS").sessions_in_range(
        "2026-01-02", "2026-06-30")]
    bars=[]
    for i,s in enumerate(days[:through+1]):
        o,h,l,c,v=99.,99.5,98.8,99.2,100.
        if i == b-1: o,h,l,c=99.5,100.,99.4,99.8
        if i == b: o,h,l,c=100.5,101.2,99.5,101.
        if b < i < retest: o,h,l,c=101.,101.4,100.5,101.1
        if i == retest: o,h,l,c=101.,101.2,100.2,100.5
        if retest < i < confirm: o,h,l,c=100.5,101.,100.6,100.8
        if i == confirm: o,h,l,c=100.5,101.5,100.3,101.3
        if i > confirm: o,h,l,c=101.2,101.6,101.,101.4
        values=dict(session=s,open=o,high=h,low=l,close=c,volume=v,
            sma20=98.,sma50=97.,sma200=90.,ema200=90.,atr14=2.,rsi14=55.,
            structure_state="bullish",trend_health="healthy",
            trend_health_source="TEST:health",short_term_phase="HEALTHY_PULLBACK",
            short_term_phase_source="TEST:phase")
        values.update((changes or {}).get(i,{}))
        if i != missing: bars.append(OpportunityFeatureBar(**values))
    view=OpportunityFeatureView(symbol="TEST",bars=bars,expected_sessions=days,
        analysis_start=days[b],indicator_seed_start=days[0],
        indicator_identity="TEST:indicators",source=SourceReference(
            source_id="TEST:daily",source_kind="TEST",sha256="TEST:sha",
            record_identity="TEST:generation",validated=True),
        price_basis="TEST:adjusted",corporate_action_version="TEST:v1",input_kind="TEST")
    ctx=CallContext(symbol="TEST",requested_as_of=days[through],
        effective_daily_session=days[through],mode="HISTORICAL",
        run_id="test",request_id="test")
    return OpportunityInput(call_context=ctx,feature_view=view,support_facts=[],
        enabled_families=list(families))


def direct(inp, prior=None):
    return detect_breakout_retest(BreakoutRetestInput(call_context=inp.call_context,
        feature_view=inp.feature_view, effective_policy=inp.breakout_policy,
        opportunity_policy=inp.effective_policy, prior_state=prior))


def test_positive_b_r_c_and_fixed_zone_math():
    r=direct(sample())
    e=r.events[0]
    assert (e.resistance,e.breakout_atr,e.breakout_line)==pytest.approx((100,2,100.2))
    assert (e.zone.lower,e.zone.upper,e.zone.invalidation_line)==pytest.approx((99.65,100.35,98.95))
    assert (e.breakout_session,e.retest_session,e.confirmation_session)==(
        r.next_state.timeline[0].session,r.timeline[2].session,r.timeline[4].session)
    assert [d.state.value for d in r.timeline[:6]]==[
        "WATCH","WATCH","CONFIRMING","CONFIRMING","ENTRY_READY","ENTRY_READY"]
    assert r.timeline[4].eligible is False and r.timeline[5].eligible is True
    assert e.entry_start==r.timeline[5].session


@pytest.mark.parametrize("close,expected",[(100.2,0),(100.200001,1)])
def test_strict_breakout_boundary(close, expected):
    assert len(direct(sample(25,changes={25:{"close":close}})).events)==expected


@pytest.mark.parametrize("volume,expected",[(90.,1),(89.999,0),(None,0)])
def test_breakout_rvol_boundary_and_missing(volume,expected):
    r=direct(sample(25,changes={25:{"volume":volume}}))
    assert len(r.events)==expected
    if volume is None:
        assert any(g.condition_id=="BREAKOUT_RVOL20" for g in r.coverage_missing_evidence)


def test_wick_only_and_breakout_day_intersection_are_not_retest():
    assert not direct(sample(25,changes={25:{"close":100.1}})).events
    r=direct(sample(25,changes={25:{"low":99.}}))
    assert r.events[0].retest_session is None
    assert "BREAKOUT_DAY_NOT_A_RETEST" in r.events[0].reason_codes


def test_fixed_anchor_survives_new_high_and_atr_expansion():
    r=direct(sample(31,changes={26:{"high":110.,"atr14":8.}}))
    e=r.events[0]
    assert (e.resistance,e.breakout_atr,e.zone.lower,e.zone.upper)==pytest.approx((100,2,99.65,100.35))


def test_a06_s15_retest_and_s17_confirmation_extends_own_deadline():
    r=direct(sample(43,retest=40,confirm=42))
    e=r.events[0]
    assert e.retest_session==r.timeline[15].session
    assert e.confirmation_session==r.timeline[17].session
    assert e.entry_start==r.timeline[18].session
    assert r.timeline[16].state=="CONFIRMING"


def test_no_retest_expires_only_on_s16_and_does_not_refresh_breakout():
    r=direct(sample(41,retest=99,confirm=100))
    assert r.timeline[15].state=="WATCH"
    assert r.timeline[16].state=="EXPIRED"
    assert len(r.events)==1


def test_confirmation_r_plus_3_allowed_r_plus_4_not_first_confirmation():
    allowed=direct(sample(31,retest=27,confirm=30))
    assert allowed.events[0].confirmation_session==allowed.timeline[5].session
    late=direct(sample(32,retest=27,confirm=31))
    assert late.events[0].confirmation_session is None
    assert late.events[0].state=="EXPIRED"


def test_close_break_invalidates_but_intraday_penetration_does_not():
    broken=direct(sample(30,changes={28:{"low":98.5,"close":98.9}}))
    assert broken.events[0].state=="INVALIDATED"
    intraday=direct(sample(28,confirm=99,changes={28:{"low":98.5,"close":99.2,"high":100.}}))
    assert intraday.events[0].state!="INVALIDATED"
    assert intraday.events[0].zone.intraday_breaches


def test_missing_gap_stops_and_resume_matches_cold():
    partial=direct(sample(30,missing=28))
    assert partial.status=="PARTIAL" and partial.evaluated_through==partial.timeline[-1].session
    prefix=direct(sample(28))
    resumed=direct(sample(32),prefix.next_state)
    cold=direct(sample(32))
    assert resumed.events==cold.events and resumed.timeline==cold.timeline
    assert resumed.result_id==cold.result_id


def test_waiting_retest_and_waiting_confirmation_restore_are_true_continuations():
    for through in (25,27):
        prefix=direct(sample(through))
        resumed=direct(sample(32),prefix.next_state)
        cold=direct(sample(32))
        assert resumed.events==cold.events
        assert resumed.timeline==cold.timeline
        assert resumed.result_id==cold.result_id
        assert resumed.next_state.state_revision==cold.next_state.state_revision


def test_corrected_committed_prefix_requires_replay():
    prior=direct(sample(27))
    corrected=sample(32,changes={24:{"high":100.1}})
    with pytest.raises(ValueError,match="BREAKOUT_PRIOR_REPLAY_REQUIRED"):
        direct(corrected,prior.next_state)


def test_terminal_event_needs_close_below_old_resistance_before_restart():
    continuous=direct(sample(48,retest=99,confirm=100))
    assert len(continuous.events)==1
    # Arm only after the terminal date, then allow a later fresh breakout.
    changes={42:{"close":99.8,"high":100.1,"low":99.5},
             44:{"open":101.5,"high":102.2,"low":101.4,"close":102.0}}
    restarted=direct(sample(45,retest=99,confirm=100,changes=changes))
    assert len(restarted.events)==2
    assert restarted.events[1].parent_breakout_id==restarted.events[0].breakout_id


def test_breakout_only_does_not_call_other_detectors(monkeypatch):
    import pcs.trend.opportunity_state as state
    monkeypatch.setattr(state,"detect_healthy_pullback",lambda **k: pytest.fail("healthy called"))
    monkeypatch.setattr(state,"detect_shallow_pullback",lambda *a,**k: pytest.fail("shallow called"))
    assert evaluate_entry_opportunity(sample()).family=="BREAKOUT_RETEST"


def test_three_family_order_and_existing_child_ids_stable():
    base=sample(families=("HEALTHY_PULLBACK","SHALLOW_PULLBACK"))
    old=evaluate_entry_opportunity(base)
    all3=evaluate_entry_opportunity(base.model_copy(update={"enabled_families":[
        "BREAKOUT_RETEST","SHALLOW_PULLBACK","HEALTHY_PULLBACK"]}))
    assert [x.family for x in all3.family_results]==[
        "HEALTHY_PULLBACK","SHALLOW_PULLBACK","BREAKOUT_RETEST"]
    assert [x.result_id for x in all3.family_results[:2]]==[x.result_id for x in old.family_results]


def test_received_time_and_future_rows_do_not_change_past_identity():
    inp=sample(29)
    r=direct(inp)
    more=sample(35)
    changed=inp.model_copy(update={"feature_view":inp.feature_view.model_copy(update={
        "bars":more.feature_view.bars,"received_at":"2030-01-01T00:00:00Z"})})
    assert direct(changed).result_id==r.result_id


def test_identity_uses_only_breakout_family_policy_dependencies():
    inp=sample(29)
    baseline=direct(inp)
    unrelated=inp.model_copy(update={"effective_policy":inp.effective_policy.model_copy(update={
        "healthy_pullback_min_pct":.07,"shallow_pullback_max_pct":.03})})
    assert direct(unrelated).result_id==baseline.result_id
    consumed=inp.model_copy(update={"effective_policy":inp.effective_policy.model_copy(update={
        "reclaim_buffer_atr":.20})})
    assert direct(consumed).result_id!=baseline.result_id


def test_typed_artifacts_views_and_queries_share_one_result(tmp_path):
    import json
    from pcs.pool.opportunities import (find_breakout_day, find_breakout_event,
        read_opportunity_bundle, write_opportunity_artifacts)
    from pcs.trend.selection_models import EntryOpportunity, OpportunityInput
    inp=sample(families=("HEALTHY_PULLBACK","SHALLOW_PULLBACK","BREAKOUT_RETEST"))
    result=evaluate_entry_opportunity(inp)
    write_opportunity_artifacts(tmp_path,[result],inputs=[inp])
    _,docs=read_opportunity_bundle(tmp_path)
    loaded=EntryOpportunity.model_validate(docs["entry_opportunities.json"][0])
    assert loaded==result and OpportunityInput.model_validate(
        docs["prepared_opportunity_inputs.json"][0])==inp
    breakout=result.family_results[2].breakout_result
    assert find_breakout_event(result,breakout.events[0].breakout_id)==breakout.events[0]
    assert find_breakout_day(result,breakout.timeline[2].session,
        breakout.events[0].zone.tests[0].test_id)
    assert json.loads((tmp_path/"breakout_events.json").read_text())[0]["resistance"]==100.
