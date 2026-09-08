"""Step 7 contract tests: real XNYS sessions, explicitly synthetic fields/prices."""
import pytest
import exchange_calendars as xc

from pcs.analysis_contracts import CallContext, SourceReference
from pcs.trend.selection_models import (OpportunityInput, OpportunityFeatureView, OpportunityFeatureBar,
    ConstructiveBaseInput, BaseState, BaseResult, BaseStructureEvidence, ConfirmedSwingEvidence)
from pcs.trend.constructive_base import detect_constructive_base
from pcs.trend.opportunity_engine import evaluate_entry_opportunity


def sample(through=45, *, changes=None, missing=(), retest=40, confirm=42, families=('CONSTRUCTIVE_BASE',)):
    sessions = [str(s.date()) for s in xc.get_calendar('XNYS').sessions_in_range('2026-01-02', '2026-10-30')]
    bars = []
    overrides = {20: (103,104,102,103), 22: (100.6,101,100,100.4), 23: (100.4,101.8,100.4,101.5),
                 29: (100.6,101,100,100.4), 30: (100.4,101.8,100.4,101.5)}
    for i, s in enumerate(sessions[:through+1]):
        o,h,l,c = (102.8,103.6,102.,103.)
        if i in overrides:
            o,h,l,c = overrides[i]
        if i >= 35:
            o,h,l,c = (102.4,102.8,102.2,102.6)
        if i == retest:
            o,h,l,c = (101.,101.2,100.2,100.5)
        if retest < i < confirm:
            o,h,l,c = (100.6,101.,100.6,100.8)
        if i == confirm:
            o,h,l,c = (100.5,101.5,100.3,101.3)
        if i > confirm:
            o,h,l,c = (101.2,101.6,101.,101.4)
        values = dict(session=s, open=o,high=h,low=l,close=c,volume=100.,atr14=2.,
            sma20=98.,sma50=97.,rsi14=55.,structure_state='bullish',trend_health='healthy',
            trend_health_source='TEST:health',short_term_phase='HEALTHY_PULLBACK',short_term_phase_source='TEST:phase')
        values.update((changes or {}).get(i, {}))
        if i not in missing:
            bars.append(OpportunityFeatureBar(**values))
    view = OpportunityFeatureView(symbol='TEST',bars=bars,expected_sessions=sessions,
        analysis_start=sessions[39],indicator_seed_start=sessions[0],indicator_identity='TEST:prepared',
        source=SourceReference(source_id='TEST:daily',source_kind='TEST',sha256='TEST:hash',record_identity='TEST:generation',validated=True),
        price_basis='TEST:adjusted',corporate_action_version='TEST:v1',input_kind='TEST')
    return OpportunityInput(call_context=CallContext(symbol='TEST',requested_as_of=sessions[through],
        effective_daily_session=sessions[through],mode='HISTORICAL',run_id='TEST',request_id='TEST'),
        feature_view=view,support_facts=[],support_result_ids={b.session.isoformat():'TEST:empty-support' for b in bars},
        enabled_families=list(families))


def direct(inp, prior=None):
    return detect_constructive_base(ConstructiveBaseInput(call_context=inp.call_context,
        feature_view=inp.feature_view,effective_policy=inp.base_policy,opportunity_policy=inp.effective_policy,
        structure_evidence=inp.base_structure_evidence,calendar=inp.calendar,prior_state=prior))


def test_manual_positive_arithmetic_and_live_separation():
    result = direct(sample())
    e = result.base_events[0]
    f = result.formation_candidates[0]
    assert (e.lower,e.upper,e.formation_atr,f.width_atr)==(100,104,2,2)
    assert (f.reference_tr_median,f.recent_tr_median)==pytest.approx((1.6,.6))
    assert len(f.tr_samples)==20 and f.held_count==2
    assert (e.lower_zone.lower,e.lower_zone.upper,e.lower_zone.invalidation_line,e.upside_exit_line)==pytest.approx((99.65,100.35,98.95,104.2))
    assert [t.test.touch_session for t in e.retrospective_tests]==[sample().feature_view.expected_sessions[i] for i in (22,29)]
    assert all(t.known_at==e.formed_at and not t.available_for_entry for t in e.retrospective_tests)
    assert all('ZONE_INTERSECTION_AFTER_AVAILABLE_AT' not in t.test.reason_codes for t in e.retrospective_tests)
    assert not direct(sample(39)).base_events[0].lower_zone.tests
    assert e.lower_zone.available_at==e.formed_at
    assert e.lower_boundary.source_type=='BASE_LOWER_BOUNDARY' and e.upper_boundary.source_type=='BASE_UPPER_BOUNDARY'
    assert [d.eligible for d in result.timeline]==[False,False,False,False,True,True,True]
    assert result.timeline[3].base_position==pytest.approx(.325)
    assert result.timeline[3].opportunity_state=='ENTRY_READY'
    assert direct(sample(46)).opportunity_state=='EXPIRED'


@pytest.mark.parametrize('through',[39,40,42])
def test_prefix_and_saved_restore(through):
    prefix=direct(sample(through)); full=direct(sample())
    assert prefix.timeline==full.timeline[:len(prefix.timeline)]
    assert prefix.transitions==full.transitions[:len(prefix.transitions)]
    restored=direct(sample(),BaseState.model_validate_json(prefix.next_state.model_dump_json()))
    assert restored.next_state==full.next_state and restored.result_id==full.result_id
    assert direct(sample(),full.next_state).result_id==full.result_id
    a=evaluate_entry_opportunity(sample(through)); b=evaluate_entry_opportunity(sample())
    assert a.timeline==b.timeline[:len(a.timeline)]


@pytest.mark.parametrize('missing,changes',[((41,),None),((41,42),None),((),{41:{'close':None}}),((39,),None)])
def test_true_partial_restore(missing,changes):
    inp=sample(missing=missing,changes=changes)
    partial=direct(inp)
    assert partial.eligible_at_requested_time is None
    assert partial.next_state.evaluated_through!=partial.timeline[-1].session
    assert direct(inp,partial.next_state).result_id==partial.result_id
    assert direct(sample(),partial.next_state).result_id==direct(sample()).result_id


def test_sliding_display_and_committed_correction():
    inp=sample()
    moved=inp.model_copy(update={'feature_view':inp.feature_view.model_copy(update={'analysis_start':inp.feature_view.expected_sessions[44]})})
    assert direct(moved,direct(sample(39)).next_state).result_id==direct(inp).result_id
    with pytest.raises(ValueError,match='BASE_PRIOR_REPLAY_REQUIRED'):
        direct(sample(changes={20:{'high':104.1}}),direct(sample(39)).next_state)


@pytest.mark.parametrize('prior_state,expected',[('neutral',False),(None,None),('bullish',True)])
def test_preceding_uptrend(prior_state,expected):
    changes={i:{'structure_state':prior_state} for i in range(20)}
    result=direct(sample(39,changes=changes))
    assert result.base_detected is expected


def test_partial_unknown_with_bullish_and_optional_details():
    result=direct(sample(39,changes={i:{'structure_state':None} for i in range(1,20)}))
    assert result.base_detected is True
    assert result.coverage_missing_evidence
    assert result.status=='COMPLETED'


@pytest.mark.parametrize('index',[19,21])
def test_missing_window_or_previous_close_is_unknown(index):
    result=direct(sample(39,missing=(index,)))
    assert result.base_detected is None


def test_true_range_uses_gap_and_no_future_price():
    inp=sample(39)
    result=direct(inp)
    tr=next(t for t in result.formation_candidates[0].tr_samples if t['session']==inp.feature_view.expected_sessions[22])
    assert tr['true_range']==3.0
    full=sample(changes={44:{'high':999.}})
    cut=full.model_copy(update={'call_context':inp.call_context})
    assert direct(cut).result_id==result.result_id


@pytest.mark.parametrize('changes',[
    {29:{'low':102.,'open':102.8,'high':103.6,'close':103.}},
    {i:{'low':100.,'open':100.2,'high':100.3,'close':100.2} for i in range(22,35)},
])
def test_not_two_independent_held(changes):
    assert direct(sample(39,changes=changes)).base_detected is False


def test_neutral_formation_does_not_fake_bullish_confirmation():
    result=direct(sample(42,changes={39:{'structure_state':'neutral'},42:{'structure_state':'neutral'}}))
    assert result.base_events and result.base_events[0].confirmation_session is None
    assert result.eligible_at_requested_time is False


@pytest.mark.parametrize('field',['atr14','volume','structure_state','trend_health','short_term_phase'])
def test_confirmation_unknown(field):
    assert direct(sample(42,changes={42:{field:None}})).eligible_at_requested_time is None


@pytest.mark.parametrize('field',['atr14','structure_state','trend_health','short_term_phase'])
def test_current_unknown(field):
    r=direct(sample(43,changes={43:{field:None}}))
    assert r.eligible_at_requested_time is None and r.current_missing_details


def test_last_touch_deadline_and_independent_confirmation_deadline():
    r=direct(sample(62,retest=59,confirm=62))
    assert r.base_events[0].retest_session==sample().feature_view.expected_sessions[59]
    assert r.base_events[0].confirmation_session==sample().feature_view.expected_sessions[62]
    late=direct(sample(62,retest=60,confirm=62))
    assert late.base_events[0].retest_session is None and late.base_events[0].state=='EXPIRED'
    assert direct(sample(44,confirm=44)).base_events[0].confirmation_session is None


def test_invalidation_priority_fixed_lines_and_intraday():
    r=direct(sample(43,changes={43:{'low':98.5,'close':98.9,'atr14':8.}}))
    assert r.opportunity_state=='INVALIDATED' and r.base_events[0].lower==100
    r=direct(sample(43,changes={43:{'low':98.5}}))
    assert r.base_events[0].lower_zone.intraday_breaches and r.base_validity=='VALID'


@pytest.mark.parametrize('close,eligible',[(102.,True),(102.0001,False)])
def test_position_boundary(close,eligible):
    r=direct(sample(43,changes={43:{'close':close,'high':102.2}}))
    assert r.eligible_at_requested_time is eligible


@pytest.mark.parametrize('close,exited',[(104.2,False),(104.20001,True)])
def test_upside_exit_strict_and_handoff(close,exited):
    r=direct(sample(40,changes={40:{'close':close,'high':104.5}}))
    assert bool(r.base_events[0].upside_exit_session)==exited
    if exited:
        assert r.base_events[0].state=='EXPIRED' and r.base_validity=='VALID'
        assert r.relationships[0].handoff=='NOT_EVALUATED'


def test_old_three_child_ids_and_policy_independence():
    inp=sample(families=('HEALTHY_PULLBACK','SHALLOW_PULLBACK','BREAKOUT_RETEST'))
    old=evaluate_entry_opportunity(inp)
    new=evaluate_entry_opportunity(inp.model_copy(update={'enabled_families':['CONSTRUCTIVE_BASE',*inp.enabled_families]}))
    assert [r.result_id for r in old.family_results]==[r.result_id for r in new.family_results[:3]]
    changed=evaluate_entry_opportunity(inp.model_copy(update={'enabled_families':new.active_families or ['CONSTRUCTIVE_BASE'],
        'base_policy':inp.base_policy.model_copy(update={'maximum_base_position':.4})}))
    assert changed.result_id!=new.family_results[-1].result_id


def test_request_time_and_old_version_rejection():
    inp=sample(43)
    ctx=inp.call_context.model_copy(update={'mode':'CURRENT_EOD','requested_as_of':'2026-05-01T16:30:00-04:00'})
    assert direct(inp.model_copy(update={'call_context':ctx})).eligible_at_requested_time is False
    ctx=ctx.model_copy(update={'requested_as_of':inp.feature_view.expected_sessions[44]+'T16:30:00-04:00'})
    r=direct(inp.model_copy(update={'call_context':ctx}))
    assert r.eligible_at_requested_time is None and r.current_missing_details
    with pytest.raises(ValueError,match='TIMEZONE_AWARE'):
        direct(inp.model_copy(update={'call_context':ctx.model_copy(update={'requested_as_of':'2026-05-01'})}))
    payload=r.model_dump(mode='json');payload['calculation_version']='constructive-base-v0'
    with pytest.raises(ValueError):
        BaseResult.model_validate(payload)


@pytest.mark.parametrize('atr,expected',[(1.,True),(.999,False)])
def test_width_four_atr_inclusive(atr,expected):
    # Position of touches and departures still satisfies independent HELD at both ATRs.
    result=direct(sample(39,changes={39:{'atr14':atr}}))
    assert next(c.predicate_value for c in result.formation_candidates[0].conditions if c.condition_id=='BASE_WIDTH_ATR') is expected


def test_zero_width_rejected_and_contraction_equality():
    changes={i:{'open':103.,'high':103.,'low':103.,'close':103.} for i in range(20,40)}
    result=direct(sample(39,changes=changes))
    assert result.base_detected is False and result.base_events==[]
    c=result.formation_candidates[0]
    assert c.reference_tr_median==c.recent_tr_median==0.
    assert next(x.predicate_value for x in c.conditions if x.condition_id=='BASE_TR_CONTRACTION') is True


def test_no_departure_and_not_yet_held_do_not_count():
    changes={i:{'open':100.7,'low':100.6,'high':101.2,'close':101.} for i in range(24,29)}
    assert direct(sample(39,changes=changes)).formation_candidates[0].held_count==1
    changes={29:{'open':102.8,'high':103.6,'low':102.,'close':103.},39:{'open':100.6,'high':101.,'low':100.,'close':100.4}}
    result=direct(sample(39,changes=changes))
    assert result.formation_candidates[0].held_count==1 and result.base_detected is False


def test_f_touch_is_retrospective_only():
    result=direct(sample(39,changes={39:{'open':101.,'high':101.2,'low':100.2,'close':100.5}}))
    assert result.base_detected is True
    assert result.base_events[0].retest_session is None
    assert not result.base_events[0].lower_zone.tests


def test_structure_details_lh_ll_and_future_confirmation():
    inp=sample(39); days=inp.feature_view.expected_sessions
    detail=BaseStructureEvidence(session=days[39],source=inp.feature_view.source,
        calculation_version='TEST:authoritative-structure',high_comparison='lower',low_comparison='lower')
    assert direct(inp.model_copy(update={'base_structure_evidence':[detail]})).base_detected is False
    detail=detail.model_copy(update={'confirmed_swings':[ConfirmedSwingEvidence(source_id='TEST:swing',
        pivot_date=days[38],confirmed_at=days[41],swing_type='low',price=100.)]})
    with pytest.raises(ValueError,match='FUTURE_SWING'):
        direct(inp.model_copy(update={'base_structure_evidence':[detail]}))


def test_reformation_requires_twenty_new_sessions_and_parent():
    original=sample(39).feature_view.bars[20:40]
    changes={47+i:{k:getattr(b,k) for k in ('open','high','low','close')} for i,b in enumerate(original)}
    assert len(direct(sample(65,changes=changes)).base_events)==1
    result=direct(sample(66,changes=changes))
    assert len(result.base_events)==2
    a,b=result.base_events
    assert b.parent_base_id==a.base_id and b.formation_window[0]>a.terminal_session
    assert b.formed_at==sample(66).feature_view.expected_sessions[66]


@pytest.mark.parametrize('volume,handoff',[(100.,'QUALIFIED'),(10.,'NOT_QUALIFIED'),(None,'UNKNOWN')])
def test_independent_breakout_handoff_does_not_change_children(volume,handoff):
    inp=sample(40,changes={40:{'open':104.,'high':105.,'low':103.8,'close':104.6,'volume':volume}},
        families=('BREAKOUT_RETEST','CONSTRUCTIVE_BASE'))
    combined=evaluate_entry_opportunity(inp)
    assert combined.relationships[0].handoff==handoff
    for child in combined.family_results:
        single=evaluate_entry_opportunity(inp.model_copy(update={'enabled_families':[child.family]}))
        assert single.result_id==child.result_id
    if handoff=='QUALIFIED':
        assert combined.relationships[0].breakout_id
    else:
        assert combined.relationships[0].breakout_id is None


def test_false_dominates_unknown_and_diagnostic_does_not_block():
    result=direct(sample(43,changes={43:{'trend_health':None,'close':102.1,'high':102.3}}))
    assert result.eligible_at_requested_time is False and result.current_missing_details
    assert direct(sample(42,changes={42:{'rsi14':None}})).base_events[0].confirmation_session


@pytest.mark.parametrize('stamp,offset',[
    ('2026-03-06T16:30:00-05:00',43),('2026-03-07T12:00:00-05:00',43),
    ('2026-03-09T10:00:00-04:00',43),
])
def test_real_xnys_current_eod(stamp,offset):
    inp=sample(43)
    ctx=inp.call_context.model_copy(update={'mode':'CURRENT_EOD','requested_as_of':stamp})
    inp=inp.model_copy(update={'call_context':ctx})
    result=direct(inp)
    assert result.requested_session==inp.feature_view.expected_sessions[offset]
    assert result.eligible_at_requested_time is True
    assert result.result_id==direct(inp,result.next_state).result_id


def test_typed_files_views_queries_and_serialized_adapter_restore(tmp_path):
    import csv,json
    from pcs.pool.opportunities import write_opportunity_artifacts,read_opportunity_bundle,opportunity_to_ai_view
    from pcs.trend.selection_models import EntryOpportunity
    from pcs.trend.constructive_base import find_base_event,find_base_day,find_base_test
    inp=sample();r=evaluate_entry_opportunity(inp)
    write_opportunity_artifacts(tmp_path,[r],inputs=[inp])
    _,docs=read_opportunity_bundle(tmp_path)
    loaded=EntryOpportunity.model_validate(docs['entry_opportunities.json'][0])
    assert evaluate_entry_opportunity(inp.model_copy(update={'prior_family_results':[loaded]})).result_id==r.result_id
    base=r.base_result;e=base.base_events[0]
    assert find_base_event(base,e.base_id)==e
    assert find_base_day(base,e.formed_at).live_test_id is None
    assert find_base_test(base,e.retrospective_tests[0].test.test_id).retrospective is True
    assert find_base_test(base,e.live_test_id).retrospective is False
    assert json.loads((tmp_path/'entry_opportunities.ai.json').read_text(encoding='utf-8'))==[opportunity_to_ai_view(r)]
    rows=list(csv.DictReader((tmp_path/'entry_opportunities.csv').read_text(encoding='utf-8').splitlines()))
    assert rows[0]['base_lower']=='100.0' and rows[0]['confirmation_deadline']==''
    assert '历史触及' in (tmp_path/'entry_opportunities.zh-CN.md').read_text(encoding='utf-8')


def test_identity_excludes_call_and_receipt():
    inp=sample();r=direct(inp)
    inp=inp.model_copy(update={'call_context':inp.call_context.model_copy(update={'run_id':'other','request_id':'other'}),
        'feature_view':inp.feature_view.model_copy(update={'received_at':'2026-09-08T17:00:00Z'})})
    assert direct(inp).result_id==r.result_id


def test_before_formation_has_no_base_identity_and_calendar_tail_is_not_clipped():
    inp=sample(45);days=inp.feature_view.expected_sessions
    early=inp.model_copy(update={'feature_view':inp.feature_view.model_copy(update={'analysis_start':days[37]})})
    result=direct(early)
    assert all(d.base_id is None for d in result.timeline[:2])
    assert result.timeline[2].formed_at==days[39]
    prefix=sample(39)
    short=prefix.model_copy(update={'feature_view':prefix.feature_view.model_copy(update={'expected_sessions':days[:40]})})
    assert direct(short).base_events[0].first_touch_deadline==days[59]


def test_corrected_full_replay_can_trace_prior_result():
    before=direct(sample())
    inp=sample(changes={20:{'high':104.1}})
    result=detect_constructive_base(ConstructiveBaseInput(call_context=inp.call_context,feature_view=inp.feature_view,
        replay_of_result_id=before.result_id))
    assert result.replay_of_result_id==before.result_id and result.result_id!=before.result_id
