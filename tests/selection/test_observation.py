"""Bounded Step 8 TEST objects; no shape/indicator engine is run."""
import json
import pytest
from pcs.analysis_contracts import CallContext,SourceReference
from pcs.trend.selection_models import (EntryOpportunity,OpportunityPolicy,OpportunityCoverage,
    OpportunityStateCheckpoint,OpportunityDay,OpportunityCondition,SupportZone,SupportTestEvent,
    SupportZoneResult,SupportZoneState,SupportZoneCoverage,SupportZonePolicy)
from pcs.selection.models import (RankingInput,BatchContext,RankingPolicy,DecisionPacketInput,EvidenceQuery,
    AIReviewInput,ReviewSubmission,Diagnostic,LegacyAssessment,SharedFact,FAMILIES)
from pcs.selection.ranking import rank_stock_opportunities,window_sessions
from pcs.selection.packets import build_decision_evidence_packet,resolve_evidence,record_ai_review
from pcs.selection.storage import (write_selection_bundle,read_selection_bundle,link_packets,
    shortlist_csv,shortlist_markdown,import_ai_review,read_ai_reviews,write_packet,read_packet)

DAY='2026-09-04'
SOURCE=SourceReference(source_id='TEST:daily',source_kind='TEST',sha256='TEST:hash',record_identity='TEST:generation',validated=True)


def condition(cid='CURRENT_DISTANCE_FROM_FIXED_ZONE',value=True,role='CURRENT_ELIGIBILITY',session=DAY):
    return OpportunityCondition(condition_id=cid,session=session,role=role,left_value=1.0,operator='<=',right_value=1.75,
        predicate_value=value,status='UNKNOWN' if value is None else 'EVALUATED',source_refs=['TEST:metric'])


def result(symbol='TEST_A',state='ENTRY_READY',eligible=True,family='HEALTHY_PULLBACK',suffix='',conditions=None):
    rid=f'TEST:{symbol}:{family}:{suffix}'
    ctx=CallContext(symbol=symbol,requested_as_of=DAY,effective_daily_session=DAY,mode='HISTORICAL',run_id='TEST',request_id='TEST')
    policy=OpportunityPolicy(family=family)
    covered=OpportunityCoverage(expected_sessions=['2026-09-03',DAY],actual_sessions=['2026-09-03',DAY],missing_sessions=[],
        analysis_start='2026-09-03',evaluated_through=DAY,indicator_seed_start='2025-01-02',indicator_identity='TEST:indicator',
        legal_input_sha256='TEST:input',source_identity='TEST:source',support_identity='TEST:support',fields={},reason_codes=[])
    checkpoint=OpportunityStateCheckpoint(symbol=symbol,episodes=[],evaluated_through=DAY,state_revision=2,input_prefix_sha256='TEST:input',
        source_identity='TEST:source',support_identity='TEST:support',policy_sha256='TEST:policy',indicator_identity='TEST:indicator',
        price_basis='TEST:adjusted',corporate_action_version='TEST:ca')
    confirmed=state=='ENTRY_READY'
    start,end=('2026-09-04','2026-09-09') if confirmed else (None,None)
    opportunity=rid+':opportunity' if state!='NO_SETUP' else None
    conditions=conditions if conditions is not None else [condition(value=eligible)] if confirmed or eligible is None else []
    day=OpportunityDay(session=DAY,state=state,capability_status='PARTIAL' if eligible is None else 'COMPLETED',
        economic_episode_id=opportunity,opportunity_id=opportunity,setup_date='2026-09-01' if opportunity else None,
        touch_date='2026-09-02' if opportunity else None,confirmation_deadline='2026-09-04' if opportunity else None,
        confirmation_date='2026-09-03' if confirmed else None,entry_start=start,entry_end=end,eligible=eligible,
        support_zone_id='TEST:zone' if opportunity else None,support_test_id='TEST:test' if opportunity else None,
        conditions=conditions,reason_codes=[])
    timeline=[day]
    if confirmed:
        timeline=[day.model_copy(update={'session':'2026-09-03','eligible':False,
            'conditions':[condition('CONFIRM_A',role='CONFIRMATION',session='2026-09-03'),
                          condition('CONFIRM_B',role='CONFIRMATION',session='2026-09-03')]}),day]
    return EntryOpportunity(symbol=symbol,as_of=DAY,status='PARTIAL' if eligible is None else 'COMPLETED',state=state,last_known_state=state,
        evaluated_through=DAY,last_known_session=DAY,entry_permitted_from=start,entry_permitted_until=end,
        confirmation_deadline_elapsed_at_requested_session=False,entry_window_elapsed_at_requested_session=False,
        eligible_at_requested_time=eligible,requested_session=DAY,request_time_semantics='HISTORICAL',economic_episode_id=opportunity,
        opportunity_id=opportunity,result_id=rid,matched_families=[family] if opportunity else [],upstream_result_ids=[],run_id='TEST',request_id='TEST',
        received_at=None,effective_policy=policy,policy_sha256='TEST:policy',call_context=ctx,episodes=[],timeline=timeline,transitions=[],
        detections=[],current_conditions=conditions,supporting_evidence=[],opposing_evidence=[],missing_evidence=[],next_observation_conditions=[],
        legacy_opinion={},opinion_differences=[],coverage=covered,next_state=checkpoint,provenance=[SOURCE],explanation='TEST only',family=family)


def input_for(results, symbols=None, families=None, **kwargs):
    return RankingInput(context=BatchContext(requested_as_of=DAY,requested_session=DAY,effective_daily_session=DAY),
        requested_symbols=symbols or sorted({r.symbol for r in results}),enabled_families=families or sorted({r.family for r in results}),
        opportunities=results,**kwargs)


def key(row,name):
    return next(k for k in row.sort_keys if k.field==name)


def test_six_groups_and_historical_ready_not_current():
    objects=[result('TEST_A'),result('TEST_B',eligible=False),result('TEST_C','WATCH',False),
        result('TEST_D','NO_SETUP',False),result('TEST_E',eligible=None),result('TEST_F','INVALIDATED',False)]
    rows=rank_stock_opportunities(input_for(objects)).rows
    assert [r.symbol for r in rows]==['TEST_A','TEST_C','TEST_B','TEST_F','TEST_D','TEST_E']
    assert [r.group for r in rows]==['READY_FOR_OPTIONS_REVIEW','WATCH_SETUP','NOT_CURRENTLY_APPLICABLE',
        'NOT_CURRENTLY_APPLICABLE','NO_SETUP','INSUFFICIENT_EVIDENCE']
    assert rows[2].family_assessments[0].state=='ENTRY_READY' and rows[2].current_eligible is False


def test_order_duplicates_and_call_metadata_do_not_change_identity():
    a,b=result(),result('TEST_B')
    inp=input_for([a,b])
    first=rank_stock_opportunities(inp)
    aggregate=a.model_copy(update={'family_results':[b,a]})
    changed=a.model_copy(update={'received_at':'2026-09-05T12:00:00Z','run_id':'different'})
    other=input_for([b,aggregate,changed],families=['HEALTHY_PULLBACK'])
    assert rank_stock_opportunities(other).shortlist_id==first.shortlist_id
    assert rank_stock_opportunities(inp.model_copy(update={'context':inp.context.model_copy(update={'run_id':'new','request_id':'new'})})).shortlist_id==first.shortlist_id


def test_ambiguous_family_requires_explicit_selection():
    a,b=result(),result(suffix='different-policy')
    b=b.model_copy(update={'policy_sha256':'TEST:other'})
    inp=input_for([a,b])
    row=rank_stock_opportunities(inp).rows[0]
    assert row.group=='INSUFFICIENT_EVIDENCE'
    assert row.family_assessments[0].execution=='EXECUTED'
    assert row.family_assessments[0].candidate_result_ids==sorted([a.result_id,b.result_id])
    choice={f'TEST_A|{DAY}|HEALTHY_PULLBACK':a.result_id}
    assert rank_stock_opportunities(inp.model_copy(update={'selected_result_ids':choice})).rows[0].group=='READY_FOR_OPTIONS_REVIEW'


@pytest.mark.parametrize('eligible,state,expected',[(True,'ENTRY_READY','READY_FOR_OPTIONS_REVIEW'),(False,'WATCH','WATCH_SETUP'),(False,'NO_SETUP','INSUFFICIENT_EVIDENCE')])
def test_known_family_and_unknown_family(eligible,state,expected):
    a=result(state=state,eligible=eligible)
    b=result(family='SHALLOW_PULLBACK',eligible=None)
    row=rank_stock_opportunities(input_for([a,b])).rows[0]
    assert row.group==expected and not row.coverage_complete


def test_confirmed_day_waits_for_next_session():
    r=result().model_copy(update={'eligible_at_requested_time':False,'entry_permitted_from':'2026-09-08','entry_permitted_until':'2026-09-10'})
    row=rank_stock_opportunities(input_for([r])).rows[0]
    assert row.group=='WATCH_SETUP'
    assert key(row,'remaining_entry_sessions').details['waiting_sessions']==1
    assert key(row,'remaining_entry_sessions').value==3


def test_real_xnys_weekend_labor_day_window():
    ctx=input_for([result()]).context
    assert window_sessions(ctx,DAY,'2026-09-09')==(3,0,[])
    assert window_sessions(ctx,'2026-09-08','2026-09-10')==(3,1,[])


def test_scope_missing_and_not_enabled_are_distinct():
    r=result(state='NO_SETUP',eligible=False)
    rows=rank_stock_opportunities(input_for([r],symbols=['TEST_A','UBER'])).rows
    assert rows[0].group=='NO_SETUP' and rows[1].group=='INSUFFICIENT_EVIDENCE'
    assert rows[0].family_assessments[1].execution=='NOT_ENABLED'
    assert rows[1].family_assessments[0].execution=='NOT_EXECUTED'
    assert rows[0].options_context.formal_evaluation_status=='NOT_EVALUATED'


def test_shared_conflict_is_local_and_family_disagreement_is_allowed():
    a,b=result(),result('TEST_B')
    facts=[SharedFact(symbol='TEST_A',identity='shared:structure',known_at=DAY,value=v,source_refs=['TEST:source']) for v in ['bullish','neutral']]
    rows={r.symbol:r for r in rank_stock_opportunities(input_for([a,b],shared_facts=facts)).rows}
    assert rows['TEST_A'].group=='INSUFFICIENT_EVIDENCE' and rows['TEST_B'].group=='READY_FOR_OPTIONS_REVIEW'
    other=result(family='SHALLOW_PULLBACK',eligible=False)
    assert rank_stock_opportunities(input_for([a,other])).rows[0].group=='READY_FOR_OPTIONS_REVIEW'


@pytest.mark.parametrize('field,value',[('requested_session','2026-09-03'),('request_time_semantics','CURRENT_EOD'),('as_of','2026-09-03')])
def test_stale_dates_do_not_become_current(field,value):
    r=result().model_copy(update={field:value})
    row=rank_stock_opportunities(input_for([r])).rows[0]
    assert row.group=='INSUFFICIENT_EVIDENCE' and 'ASSESSMENT_TIME_MISMATCH' in row.reason_codes


def test_cross_family_identity_mismatch():
    a,b=result(),result(family='SHALLOW_PULLBACK')
    b=b.model_copy(update={'next_state':b.next_state.model_copy(update={'price_basis':'other'})})
    row=rank_stock_opportunities(input_for([a,b])).rows[0]
    assert 'SHARED_INPUT_IDENTITY_CONFLICT:price_basis' in row.reason_codes


def test_confirmation_fraction_is_saved_role_only_and_diagnostic_excluded():
    r=result()
    first=r.timeline[0].model_copy(update={'conditions':[condition('A',True,'CONFIRMATION','2026-09-03'),
        condition('B',False,'CONFIRMATION','2026-09-03'),condition('C',None,'CONFIRMATION','2026-09-03'),
        condition('RSI',True,'DIAGNOSTIC','2026-09-03')]})
    r=r.model_copy(update={'timeline':[first,r.timeline[-1]]})
    k=key(rank_stock_opportunities(input_for([r])).rows[0],'confirmation_fraction')
    assert k.value==pytest.approx(1/3) and k.details['total']==3 and k.details['unknown']==1
    r=r.model_copy(update={'timeline':[r.timeline[-1]]})
    assert key(rank_stock_opportunities(input_for([r])).rows[0],'confirmation_fraction').value is None


def test_liquidity_compatibility_and_null_last():
    a,b=result(),result('TEST_B')
    def diagnostic(symbol,value,currency='USD'):
        return Diagnostic(symbol=symbol,metric_id='dollar_volume_median_20',result_id='TEST:profile:'+symbol,as_of=DAY,
            value=value,unit=currency+'/session',currency=currency,price_basis='TEST:adjusted',validated=True,source_refs=['TEST:metric'])
    inp=input_for([a,b],diagnostics=[diagnostic('TEST_B',100),diagnostic('TEST_A',1000,'EUR')])
    rows=rank_stock_opportunities(inp).rows
    assert rows[0].symbol=='TEST_B' and key(rows[1],'dollar_volume_median_20').value is None


def test_scope_policy_changes_and_source_id_unchanged():
    a,b=result(),result('TEST_B')
    inp=input_for([a,b]); original=rank_stock_opportunities(inp)
    single=rank_stock_opportunities(inp.model_copy(update={'requested_symbols':['TEST_A']}))
    assert original.shortlist_id!=single.shortlist_id and original.rows[0].row_id==single.rows[0].row_id
    policy=RankingPolicy(liquidity_currency='EUR')
    modified=rank_stock_opportunities(inp.model_copy(update={'policy':policy}))
    assert original.shortlist_id!=modified.shortlist_id and a.result_id==original.rows[0].representative_result_id


def test_legacy_wait_is_not_stock_failure():
    a=result()
    old=LegacyAssessment(symbol=a.symbol,as_of=DAY,result_id='TEST:old',timing_eligible=True,action='WAIT',options={'status':'NOT_EVALUATED'})
    assert rank_stock_opportunities(input_for([a],legacy=[old])).rows[0].old_new_comparison['status']=='OLD_AND_NEW'
    old=old.model_copy(update={'timing_eligible':None})
    assert rank_stock_opportunities(input_for([a],legacy=[old])).rows[0].old_new_comparison['status']=='INCOMPLETE_EVIDENCE'


def test_independent_rejected_packet_query_and_move(tmp_path):
    inp=input_for([result('AAL','NO_SETUP',False,conditions=[condition('REQUIRED_FAILED',False,'DISCOVERY')])])
    packet=build_decision_evidence_packet(DecisionPacketInput(symbol='AAL',selection_input=inp))
    assert packet.shortlist_id is None and packet.opposing_evidence
    ref=packet.opposing_evidence[0]
    assert resolve_evidence(EvidenceQuery(packet=packet,evidence_id=ref.evidence_id)).status=='RESOLVED'
    assert resolve_evidence(EvidenceQuery(packet=packet,evidence_id='other-symbol')).status=='NOT_FOUND'
    write_packet(tmp_path/'first',packet)
    (tmp_path/'first').rename(tmp_path/'moved')
    loaded=read_packet(tmp_path/'moved')
    assert resolve_evidence(EvidenceQuery(packet=loaded,evidence_id=ref.evidence_id)).status=='RESOLVED'
    missing=build_decision_evidence_packet(DecisionPacketInput(symbol='MISSING',selection_input=inp))
    assert missing.status=='NOT_IN_INPUT_SCOPE' and not missing.detail_index


def submission(packet,**changes):
    values=dict(packet_id=packet.packet_id,packet_content_identity=packet.content_identity,generated_at='2026-09-09T12:00:00Z',
        origin='TEST',recommendation='Further stock research despite program rejection',scope='STOCK_RESEARCH',
        supporting_refs=[packet.detail_index[0].evidence_id],disagreements_with_program=['TEST disagreement'])
    values.update(changes)
    return ReviewSubmission(**values)


def test_review_validation_idempotence_and_separation(tmp_path):
    inp=input_for([result(eligible=False)])
    packet=build_decision_evidence_packet(DecisionPacketInput(symbol='TEST_A',selection_input=inp))
    before=packet.model_dump_json(); s=submission(packet)
    review=import_ai_review(tmp_path/'reviews',packet,s)
    assert import_ai_review(tmp_path/'reviews',packet,s)==review
    assert read_ai_reviews(tmp_path/'reviews')==[review]
    assert packet.model_dump_json()==before and review.origin=='TEST' and not review.model_called_by_pcs
    assert packet.ai_review_status=='NOT_REVIEWED' and review.user_decision_status=='NOT_RECORDED'
    with pytest.raises(ValueError,match='REVIEW_ID_CONTENT_CONFLICT'):
        import_ai_review(tmp_path/'reviews',packet,s.model_copy(update={'review_id':review.review_id,'recommendation':'different'}))
    for update in [{'packet_id':'wrong'},{'supporting_refs':['absent']}]:
        with pytest.raises(ValueError):
            record_ai_review(AIReviewInput(packet=packet,submission=s.model_copy(update=update)))
    new=build_decision_evidence_packet(DecisionPacketInput(symbol='TEST_A',selection_input=input_for([result()])))
    with pytest.raises(ValueError,match='REVIEW_PACKET_IDENTITY_MISMATCH'):
        record_ai_review(AIReviewInput(packet=new,submission=s))


def test_distinct_review_ids_with_identical_content_are_append_only(tmp_path):
    packet=build_decision_evidence_packet(DecisionPacketInput(symbol='TEST_A',selection_input=input_for([result()])))
    root=tmp_path/'reviews'
    first=import_ai_review(root,packet,submission(packet,review_id='TEST:first'))
    manifest=json.loads((root/'review_manifest.json').read_text())
    first_file=root/manifest['reviews'][first.review_id]['file']
    original=first_file.read_bytes()
    second=import_ai_review(root,packet,submission(packet,review_id='TEST:second'))
    assert first.content_sha256==second.content_sha256
    assert first_file.read_bytes()==original
    assert {r.review_id:r for r in read_ai_reviews(root)}=={first.review_id:first,second.review_id:second}
    before=(root/'review_manifest.json').read_bytes()
    assert import_ai_review(root,packet,submission(packet,review_id='TEST:first'))==first
    with pytest.raises(ValueError,match='REVIEW_ID_CONTENT_CONFLICT'):
        import_ai_review(root,packet,submission(packet,review_id='TEST:second',recommendation='changed'))
    assert (root/'review_manifest.json').read_bytes()==before and first_file.read_bytes()==original


@pytest.mark.parametrize('state,flag',[('WATCH','confirmation_deadline_elapsed_at_requested_session'),
    ('CONFIRMING','confirmation_deadline_elapsed_at_requested_session'),
    ('ENTRY_READY','entry_window_elapsed_at_requested_session')])
def test_saved_expiry_overrides_historical_state_and_preserves_other_family(state,flag):
    day='2026-09-08'
    r=result(state=state,eligible=False)
    r=r.model_copy(update={'as_of':day,'requested_session':day,flag:True})
    context=BatchContext(requested_as_of=day,requested_session=day,effective_daily_session=day)
    inp=input_for([r]).model_copy(update={'context':context})
    row=rank_stock_opportunities(inp).rows[0]
    family=row.family_assessments[0]
    assert row.group=='NOT_CURRENTLY_APPLICABLE' and row.current_eligible is False
    assert family.state==state and getattr(family,flag) is True
    assert any('ELAPSED_AT_REQUEST' in reason for reason in family.reason_codes)
    other=result(family='SHALLOW_PULLBACK').model_copy(update={'as_of':day,'requested_session':day})
    mixed=inp.model_copy(update={'opportunities':[r,other],'enabled_families':['HEALTHY_PULLBACK','SHALLOW_PULLBACK']})
    assert rank_stock_opportunities(mixed).rows[0].group=='READY_FOR_OPTIONS_REVIEW'


def test_actual_sort_references_resolve_with_support_tests_and_current_only_conditions(tmp_path):
    r=result().model_copy(update={'current_conditions':[condition('CURRENT_ONLY',False)]})
    test=held('T1','2026-09-01','2026-09-02')
    support=support_for(r,[test])
    diagnostic=Diagnostic(symbol=r.symbol,metric_id='dollar_volume_median_20',result_id='TEST:profile',as_of=DAY,
        value=100,unit='USD/session',currency='USD',price_basis='TEST:adjusted',validated=True,source_refs=['TEST:upstream'])
    inp=input_for([r],supports=[support],diagnostics=[diagnostic])
    row=rank_stock_opportunities(inp).rows[0]
    packet=build_decision_evidence_packet(DecisionPacketInput(symbol=r.symbol,selection_input=inp))
    write_packet(tmp_path/'packet',packet)
    (tmp_path/'packet').rename(tmp_path/'moved')
    packet=read_packet(tmp_path/'moved')
    refs=[ref for k in row.sort_keys for ref in k.source_refs]
    assert 'T1' in refs and any(':CONFIRM_A' in ref for ref in refs)
    for ref in refs:
        query=resolve_evidence(EvidenceQuery(packet=packet,evidence_id=ref))
        assert query.status=='RESOLVED',(ref,query.reason_codes)
    query=resolve_evidence(EvidenceQuery(packet=packet,evidence_id=f'{r.result_id}:{DAY}:CURRENT_ONLY'))
    assert query.status=='RESOLVED' and query.record.value['predicate_value'] is False
    assert query.record.ref.json_pointer.startswith('/current_conditions/')
    for component in packet.component_refs:
        assert resolve_evidence(EvidenceQuery(packet=packet,evidence_id=component['result_id'])).status=='RESOLVED'


def test_conflicting_condition_alias_requires_exact_pointer():
    r=result().model_copy(update={'current_conditions':[condition(value=False)]})
    packet=build_decision_evidence_packet(DecisionPacketInput(symbol=r.symbol,selection_input=input_for([r])))
    alias=f'{r.result_id}:{DAY}:CURRENT_DISTANCE_FROM_FIXED_ZONE'
    assert resolve_evidence(EvidenceQuery(packet=packet,evidence_id=alias)).reason_codes==['AMBIGUOUS_EVIDENCE_ID']
    exact=resolve_evidence(EvidenceQuery(packet=packet,evidence_id=f'{r.result_id}:/current_conditions/0'))
    assert exact.status=='RESOLVED' and exact.record.value['predicate_value'] is False


@pytest.mark.parametrize('collision',['discovery','current','identical'])
def test_actual_confirmation_sort_refs_disambiguate_saved_conditions(collision):
    r=result()
    confirmation=r.timeline[0].conditions[0]
    other=confirmation if collision=='identical' else confirmation.model_copy(update={
        'role':'DISCOVERY' if collision=='discovery' else 'CURRENT_ELIGIBILITY','left_value':0.5})
    first=r.timeline[0].model_copy(update={'conditions':[other,*r.timeline[0].conditions]})
    r=r.model_copy(update={'timeline':[first,*r.timeline[1:]]}) if collision!='current' else r.model_copy(update={'current_conditions':[other]})
    inp=input_for([r])
    row=rank_stock_opportunities(inp).rows[0]
    packet=build_decision_evidence_packet(DecisionPacketInput(symbol=r.symbol,selection_input=inp))
    confirmation_key=key(row,'confirmation_fraction')
    assert confirmation_key.value==1.0
    for ref in confirmation_key.source_refs:
        query=resolve_evidence(EvidenceQuery(packet=packet,evidence_id=ref))
        assert query.status=='RESOLVED'
        assert query.record.value['role']=='CONFIRMATION'
        assert query.record.value['left_value']==1.0
    alias=f'{r.result_id}:2026-09-03:CONFIRM_A'
    if collision=='identical':
        assert alias in confirmation_key.source_refs
    else:
        assert alias not in confirmation_key.source_refs
        assert resolve_evidence(EvidenceQuery(packet=packet,evidence_id=alias)).reason_codes==['AMBIGUOUS_EVIDENCE_ID']


def test_v1_outputs_cannot_silently_reuse_v2_semantics():
    from pcs.selection.models import StockShortlist,DecisionEvidencePacket
    inp=input_for([result()])
    ranked=rank_stock_opportunities(inp)
    packet=build_decision_evidence_packet(DecisionPacketInput(symbol='TEST_A',selection_input=inp))
    assert ranked.calculation_version=='stock-observation-ranking-v2'
    assert packet.calculation_version=='decision-evidence-packet-v2'
    for obj,model,old in [(ranked,StockShortlist,'stock-observation-ranking-v1'),
        (packet,DecisionEvidencePacket,'decision-evidence-packet-v1')]:
        raw=obj.model_dump(mode='json');raw.update(version='1.0',calculation_version=old)
        with pytest.raises(ValueError):
            model.model_validate(raw)


def test_bundle_roundtrip_views_and_interrupted_publish(tmp_path,monkeypatch):
    inp=input_for([result()])
    packet=build_decision_evidence_packet(DecisionPacketInput(symbol='TEST_A',selection_input=inp))
    ranked=link_packets(rank_stock_opportunities(inp),[packet])
    root=write_selection_bundle(tmp_path/'complete',ranked,[packet])
    read,packets,_=read_selection_bundle(root)
    assert read==ranked and packets==[packet]
    assert (root/'stock_shortlist.csv').read_text(encoding='utf-8').splitlines()==shortlist_csv(ranked).splitlines()
    assert (root/'stock_shortlist.zh-CN.md').read_text(encoding='utf-8')==shortlist_markdown(ranked)
    assert json.loads((root/'stock_shortlist.ai.json').read_text(encoding='utf-8'))==ranked.model_dump(mode='json')
    import pcs.selection.storage as storage
    write=storage._write_atomic
    def fail_manifest(path,value):
        if path.name=='artifact_manifest.json': raise RuntimeError('TEST interruption')
        return write(path,value)
    monkeypatch.setattr(storage,'_write_atomic',fail_manifest)
    with pytest.raises(RuntimeError): write_selection_bundle(tmp_path/'interrupted',ranked,[packet])
    assert not (tmp_path/'interrupted').exists()
    with pytest.raises(FileNotFoundError): read_selection_bundle(tmp_path/'interrupted')


def test_shortlist_changes_do_not_invent_strategy_weakness():
    a,b=result(),result('TEST_B')
    old=rank_stock_opportunities(input_for([a,b]))
    inp=input_for([],symbols=['TEST_A'],families=['HEALTHY_PULLBACK'],previous=old)
    changes={c['symbol']:c['reason_codes'] for c in rank_stock_opportunities(inp).changes}
    assert 'SCOPE_REMOVED' in changes['TEST_B'] and 'INVALIDATED' not in changes['TEST_B']
    assert 'EVIDENCE_UNAVAILABLE' in changes['TEST_A'] and 'INVALIDATED' not in changes['TEST_A']


def support_for(r,tests,*,zone_id='TEST:zone',kind='SWING_LOW',available='2026-08-01'):
    zone=SupportZone(zone_id=zone_id,symbol=r.symbol,zone_type=kind,lower=99,upper=101,anchor_price=100,anchor_atr=2,
        invalidation_line=98,formed_at=available,available_at=available,creation_sources=[],observed_source_ids=[],
        price_basis='TEST:adjusted',corporate_action_version='TEST:ca',policy_id='TEST:support',calculation_version='support-zones-v2',
        state='REPEATED_HELD_TESTS',evidence_grade='REPEATED_HELD',tests=tests,intraday_breaches=[],reason_codes=[])
    coverage=SupportZoneCoverage(expected_sessions=[DAY],actual_sessions=[DAY],missing_sessions=[],analysis_start=DAY,
        evaluated_through=DAY,indicator_seed_start='2025-01-02',indicator_identity='TEST:different-indicator-wrapper',
        legal_input_sha256='TEST:input',source_identity='TEST:source',fields={},reason_codes=[])
    state=SupportZoneState(symbol=r.symbol,zones=[zone],support_history=[],evaluated_through=DAY,state_revision=1,
        input_prefix_sha256='TEST:input',source_identity='TEST:source',price_basis='TEST:adjusted',corporate_action_version='TEST:ca',
        policy_sha256='TEST:policy',indicator_identity=coverage.indicator_identity)
    return SupportZoneResult(symbol=r.symbol,as_of=DAY,status='COMPLETED',data_timestamp=None,received_at=None,run_id='TEST',request_id='TEST',
        result_id='TEST:support-result',reason_codes=[],call_context=r.call_context,effective_policy=SupportZonePolicy(),policy_sha256='TEST:policy',
        current_zones=[zone],archived_zones=[],support_history=[],state_changes=[],selections=[],unselected_zones=[],coverage=coverage,
        next_state=state,provenance=[SOURCE],explanation='TEST support')


def held(test_id,touch,held_at,departure=None):
    return SupportTestEvent(test_id=test_id,touch_session=touch,touch_low=99,touch_high=101,cumulative_low=99,
        confirmation_deadline=held_at,status='HELD',first_held_at=held_at,ended_at=held_at,departure_session=departure,
        penetration_atr=0,reason_codes=[])


@pytest.mark.parametrize('kind,available,zone_id,expected',[
    ('SWING_LOW','2026-08-01','TEST:zone','MULTIPLE_INDEPENDENT_HELD'),
    ('SWING_LOW','2026-08-01','OTHER:zone','UNKNOWN'),
    ('BASE_LOWER_BOUNDARY','2026-09-01','TEST:zone','UNKNOWN'),
])
def test_bound_live_tests_not_other_zone_or_retrospective(kind,available,zone_id,expected):
    r=result()
    tests=[held('T1','2026-08-20','2026-08-21','2026-08-24'),held('T2','2026-09-01','2026-09-02')]
    support=support_for(r,tests,zone_id=zone_id,kind=kind,available=available)
    row=rank_stock_opportunities(input_for([r],supports=[support])).rows[0]
    assert key(row,'live_support_class').details['category']==expected
    if expected=='UNKNOWN': assert key(row,'live_support_class').value is None


def test_future_held_is_not_known_and_tests_need_departure():
    r=result()
    support=support_for(r,[held('T1','2026-09-03','2026-09-08')])
    row=rank_stock_opportunities(input_for([r],supports=[support])).rows[0]
    assert key(row,'live_support_class').details['category']=='IN_PROGRESS'
    support=support_for(r,[held('T1','2026-08-20','2026-08-21'),held('T2','2026-09-01','2026-09-02')])
    assert key(rank_stock_opportunities(input_for([r],supports=[support])).rows[0],'live_support_class').details['category']=='SINGLE_HELD'


def test_representative_confirmation_date_not_family_name():
    a=result(family='HEALTHY_PULLBACK');b=result(family='SHALLOW_PULLBACK')
    a=a.model_copy(update={'timeline':[a.timeline[-1].model_copy(update={'confirmation_date':'2026-09-02'})]})
    inp=input_for([a,b])
    row=rank_stock_opportunities(inp).rows[0]
    assert row.representative_result_id==b.result_id and len(row.representative_candidates)==2
    assert len(row.active_families)==2


def test_sort_policy_directions_are_actual_and_scope_diff_recorded():
    a,b=result(),result('TEST_B')
    inp=input_for([a,b]);old=rank_stock_opportunities(inp)
    # Symbol DESC is an explicit policy override, not a hidden favourite-family weight.
    fields=[f.model_copy(update={'direction':'DESC'}) if f.field=='symbol' else f for f in inp.policy.sort_fields]
    changed=inp.model_copy(update={'policy':inp.policy.model_copy(update={'sort_fields':fields}),'previous':old})
    new=rank_stock_opportunities(changed)
    assert new.rows[0].symbol=='TEST_B'
    assert all('POLICY_CHANGED' in c['reason_codes'] for c in new.changes)


def test_query_batch_hash_tamper_and_new_date_timestamp():
    inp=input_for([result()])
    packet=build_decision_evidence_packet(DecisionPacketInput(symbol='TEST_A',selection_input=inp))
    query=resolve_evidence(EvidenceQuery(packet=packet,evidence_ids=[r.evidence_id for r in packet.detail_index]))
    assert query.status=='RESOLVED' and len(query.records)==len(packet.detail_index)
    changed=packet.model_copy(update={'as_of':'2026-09-09'})
    assert resolve_evidence(EvidenceQuery(packet=changed,evidence_id=packet.detail_index[0].evidence_id)).status=='UNRESOLVED'
    newctx=inp.context.model_copy(update={'requested_as_of':'2026-09-09','requested_session':'2026-09-09','effective_daily_session':'2026-09-09'})
    new=rank_stock_opportunities(inp.model_copy(update={'context':newctx}))
    assert new.data_timestamp==DAY and new.rows[0].group=='INSUFFICIENT_EVIDENCE'


def test_shared_structure_conflict_is_detected_from_actual_conditions():
    a=result(conditions=[condition('STRUCTURE_STILL_BULLISH').model_copy(update={'left_value':'bullish'})])
    b=result(family='SHALLOW_PULLBACK',conditions=[condition('STRUCTURE_STILL_BULLISH').model_copy(update={'left_value':'neutral'})])
    row=rank_stock_opportunities(input_for([a,b])).rows[0]
    assert row.group=='INSUFFICIENT_EVIDENCE' and 'SHARED_REQUIRED_EVIDENCE_CONFLICT' in row.reason_codes


def test_invalidation_predicate_polarity_and_same_id_collision():
    safe=condition('STRUCTURE_BEARISH_INVALIDATION',False,'INVALIDATION')
    a=result(conditions=[safe])
    row=rank_stock_opportunities(input_for([a])).rows[0]
    assert row.supporting_evidence[0]['predicate_value'] is False and not row.opposing_evidence
    collision=a.model_copy(update={'state':type(a.state).INVALIDATED})
    assert 'RESULT_ID_CONTENT_CONFLICT' in rank_stock_opportunities(input_for([a,collision])).rows[0].reason_codes


def test_saved_terminal_true_cannot_be_promoted_and_legacy_mismatch_retained():
    a=result(state='INVALIDATED',eligible=True)
    assert rank_stock_opportunities(input_for([a])).rows[0].group=='INSUFFICIENT_EVIDENCE'
    old=LegacyAssessment(symbol=a.symbol,as_of='2026-09-03',result_id='TEST:old',timing_eligible=True,action='WAIT')
    comparison=rank_stock_opportunities(input_for([a],legacy=[old])).rows[0].old_new_comparison
    assert comparison['legacy']['action']=='WAIT' and comparison['status']=='INCOMPLETE_EVIDENCE'


def test_attaching_packet_does_not_change_ranking_identity():
    inp=input_for([result()]);ranked=rank_stock_opportunities(inp)
    packet=build_decision_evidence_packet(DecisionPacketInput(symbol='TEST_A',selection_input=inp))
    assert link_packets(ranked,[packet]).shortlist_id==ranked.shortlist_id


@pytest.mark.parametrize('with_breakout',[False,True])
def test_saved_adapter_selects_old_children_before_typed_validation(tmp_path,with_breakout):
    from pcs.trend.selection_models import OpportunityInput,OpportunityFeatureView
    from pcs.pool.opportunities import write_opportunity_artifacts
    from pcs.selection.adapters import SelectionInputManifest,BundleSelection,load_selection_input
    from hashlib import sha256
    r=result()
    if with_breakout:
        from pcs.trend.selection_models import BreakoutRetestResult,BreakoutRetestState,BreakoutRetestPolicy
        state=BreakoutRetestState(analysis_start=DAY,symbol=r.symbol,events=[],timeline=[],evaluated_through=DAY,
            input_prefix_sha256='TEST:input',source_identity='TEST:source',policy_identity='TEST:policy',
            indicator_identity='TEST:indicator',price_basis='TEST:adjusted',corporate_action_version='TEST:ca',state_revision=1)
        breakout=BreakoutRetestResult(symbol=r.symbol,as_of=DAY,status='COMPLETED',run_id='TEST',request_id='TEST',
            result_id='TEST:breakout',reason_codes=[],call_context=r.call_context,effective_policy=BreakoutRetestPolicy(),
            policy_sha256='TEST:policy',events=[],timeline=[],eligible_at_requested_time=True,requested_session=DAY,
            request_time_semantics='HISTORICAL',evaluated_through=DAY,missing_sessions=[],coverage_missing_evidence=[],
            next_state=state,provenance=[SOURCE],explanation='TEST saved schema without calendar')
        r=r.model_copy(update={'breakout_result':breakout})
        from pcs.selection.ranking import binding_for
        assert binding_for(r,[]).calendar=='XNYS'
    view=OpportunityFeatureView(symbol=r.symbol,bars=[],expected_sessions=[DAY],analysis_start=DAY,indicator_seed_start=DAY,
        indicator_identity='TEST:indicator',source=SOURCE,price_basis='TEST:adjusted',corporate_action_version='TEST:ca',input_kind='TEST')
    saved_input=OpportunityInput(call_context=r.call_context,feature_view=view,support_facts=[])
    root=tmp_path/'source'
    write_opportunity_artifacts(root,[r],inputs=[saved_input])
    path=root/'entry_opportunities.json'
    # Deliberately un-loadable old platform child must remain unconsumed historical JSON.
    raw=r.model_dump(mode='json')
    raw['family_results']=[r.model_dump(mode='json'),{'symbol':r.symbol,'family':'CONSTRUCTIVE_BASE',
        'result_id':'TEST:old-platform','base_result':{'calculation_version':'constructive-base-v1'}}]
    path.write_text(json.dumps([raw]),encoding='utf-8')
    manifest=json.loads((root/'artifact_manifest.json').read_text(encoding='utf-8'))
    manifest['sha256']['entry_opportunities.json']=sha256(path.read_bytes()).hexdigest()
    manifest['tracked_source_dirty']=False  # TEST synthetic bundle, not canonical readiness.
    (root/'artifact_manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    spec=SelectionInputManifest(context=input_for([r]).context,requested_symbols=[r.symbol],enabled_families=['HEALTHY_PULLBACK'],
        bundles=[BundleSelection(bundle_id='TEST:old',path=str(root),kind='OPPORTUNITIES',families=['HEALTHY_PULLBACK'])])
    loaded=load_selection_input(spec)
    assert [x.result_id for x in loaded.ranking_input.opportunities]==[r.result_id]
    assert loaded.read_audit['package_read_count']==1
    path.write_text('[]',encoding='utf-8')
    bad=load_selection_input(spec)
    assert bad.ranking_input.failures and not bad.ranking_input.opportunities


def test_cli_explicit_mode_and_old_evidence_export(monkeypatch):
    import sys
    from pcs.cli import main
    from pcs.pool.ai_evidence import build_decision_evidence_packet as exported
    assert exported is build_decision_evidence_packet
    monkeypatch.setattr(sys,'argv',['pcs','pool-evidence','--discussion-packet','--symbol','AAL'])
    with pytest.raises(SystemExit,match='SELECTION_DIRECTORY_REQUIRED'):
        main()
