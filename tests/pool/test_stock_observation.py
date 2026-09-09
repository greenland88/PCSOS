"""11A integration tests: small TEST sequences, no provider/whole-pool scans."""
import json
import runpy
from pathlib import Path
from time import perf_counter
import pytest

from pcs.pool.observation_models import (StockObservationInput,SelectionProfile,ObservationBudgets,ObservationQuery)
from pcs.pool.observation_components import PreparedObservation,compatible_prefix
from pcs.pool.observation_storage import ObservationStore,read_ref,read_json
from pcs.pool.observation import read_stock_observation
from pcs.pool.runner import run_pcs_pool
from pcs.selection.models import BatchContext
from pcs.selection.identity import digest
from pcs.trend.selection_models import DailyFeatureView,DailyBar,SupportFeatureView,SupportFeatureBar,ProfileInput

sample=runpy.run_path(str(Path(__file__).parents[1]/'trend/test_constructive_base.py'))['sample']


class TestAdapter:
    __test__=False
    input_kind='TEST'
    def __init__(self,through=43,changes=None,blocked=()):
        self.input=sample(through,changes=changes)
        self.counts={'reads':0,'prepares':0,'benchmark':0}
        self.blocked=blocked
    def load_benchmark(self):self.counts['benchmark']+=1
    def verify(self,symbol,previous=None):
        self.counts['reads']+=1
        if symbol in self.blocked:raise ValueError('TEST_DAILY_MISSING')
        return None,digest(self.input.feature_view)
    def prepare(self,symbol,verified,previous=None):
        self.counts['prepares']+=1
        inp=self.input
        view=inp.feature_view
        daily=DailyFeatureView(symbol=symbol,bars=[DailyBar(**{k:v for k,v in b.model_dump().items() if k in DailyBar.model_fields}) for b in view.bars],
            source=view.source,price_basis=view.price_basis,corporate_action_version=view.corporate_action_version,input_kind='TEST')
        support=SupportFeatureView(symbol=symbol,bars=[SupportFeatureBar(**{k:v for k,v in b.model_dump().items() if k in SupportFeatureBar.model_fields}) for b in view.bars if str(b.session)>=view.analysis_start],
            confirmed_swings=[],expected_sessions=view.expected_sessions,analysis_start=view.analysis_start,
            indicator_seed_start=view.indicator_seed_start,indicator_identity=view.indicator_identity,source=view.source,
            price_basis=view.price_basis,corporate_action_version=view.corporate_action_version,input_kind='TEST')
        return PreparedObservation(opportunity=inp,support_view=support,profile_input=ProfileInput(call_context=inp.call_context,feature_view=daily),
            data_identity=verified[1],audit={'TEST':True})
    def release(self,symbols):pass
    def verify_unchanged(self):return {'status':'TEST_NO_CANONICAL'}


def spec(tmp_path,adapter,**updates):
    day=adapter.input.call_context.effective_daily_session
    values=dict(symbols=['TEST'],context=BatchContext(requested_as_of=day,requested_session=day,effective_daily_session=day),
        selection_profile=SelectionProfile(families=['CONSTRUCTIVE_BASE']),output_directory=str(tmp_path),run_id='test-run',
        budgets=ObservationBudgets(total_seconds=60))
    values.update(updates)
    return StockObservationInput(**values)


def run(inp,adapter):return run_pcs_pool(mode='EOD',scope='STOCK_OBSERVATION',observation_input=inp,observation_adapter=adapter)


def test_observation_before_legacy_and_same_request_reuses(tmp_path,monkeypatch):
    import pcs.pool.runner as runner
    monkeypatch.setattr(runner,'_evaluate_symbol',lambda *a,**k:pytest.fail('legacy strategy called'))
    adapter=TestAdapter();inp=spec(tmp_path,adapter)
    first=run(inp,adapter)
    assert first.selection_v2['groups']['READY_FOR_OPTIONS_REVIEW']==1
    assert first.options_calls==0 and all(v is None for v in first.legacy_counts.values())
    before=read_stock_observation(ObservationQuery(run_directory=first.output_directory,symbol='TEST')).packet
    second=run(inp.model_copy(update={'resume_run_id':inp.run_id}),adapter)
    after=read_stock_observation(ObservationQuery(run_directory=second.output_directory,symbol='TEST')).packet
    assert before.packet_id==after.packet_id and adapter.counts['prepares']==1
    assert second.selection_v2['cache_hits']>=4


def test_cold_and_continued_component_id_and_confirmation(tmp_path):
    a=TestAdapter(40);first=run(spec(tmp_path,a),a)
    b=TestAdapter(43);second=run(spec(tmp_path,b,run_id='updated',previous_run=first.output_directory),b)
    c=TestAdapter(43);cold=run(spec(tmp_path,c,run_id='cold'),c)
    def opportunity(run):
        state=read_stock_observation(ObservationQuery(run_directory=run.output_directory,symbol='TEST')).state
        return read_ref(run.output_directory,state.components['CONSTRUCTIVE_BASE'])
    assert opportunity(second)['result_id']==opportunity(cold)['result_id']
    assert opportunity(second)['base_result']['base_events'][0]['confirmation_session']==opportunity(cold)['base_result']['base_events'][0]['confirmation_session']


def test_atomic_object_without_checkpoint_and_stale_attempt(tmp_path):
    from pcs.pool.observation_models import ObservationSymbol
    store=ObservationStore(tmp_path,'spec','run')
    state=ObservationSymbol(symbol='TEST')
    def crash(_):raise RuntimeError('TEST crash after immutable write')
    with pytest.raises(RuntimeError,match='TEST crash'):
        store.commit(state,'PROFILE','dep',{'result_id':'TEST'},token=store.token,expected_revision=0,
            deadline=perf_counter()+10,after_object=crash)
    assert not store.state('TEST').components and list((tmp_path/'objects').glob('*.json'))
    resumed=ObservationStore(tmp_path,'spec','run',resume=True)
    with pytest.raises(ValueError,match='STALE_ATTEMPT'):
        store.commit(state,'PROFILE','dep',{},token=store.token,expected_revision=0,deadline=perf_counter()+10)
    good=resumed.commit(state,'PROFILE','dep',{'result_id':'TEST'},token=resumed.token,expected_revision=0,deadline=perf_counter()+10)
    assert good.components['PROFILE'].revision==1


def test_data_blocked_scope_and_partial_not_current(tmp_path):
    adapter=TestAdapter(blocked=['MISSING'])
    result=run(spec(tmp_path,adapter,symbols=['TEST','MISSING']),adapter)
    assert result.summary['data_blocked_count']==1 and result.summary['execution_completed_count']==1
    assert sum(result.selection_v2['groups'].values())==2 and result.coverage=='PARTIAL'
    assert not (tmp_path/'CURRENT.json').exists()
    query=read_stock_observation(ObservationQuery(run_directory=result.output_directory,symbol='MISSING'))
    assert query.packet and query.packet.current_gaps
    assert read_stock_observation(ObservationQuery(run_directory=result.output_directory,symbol='OTHER')).status=='NOT_IN_INPUT_SCOPE'


def test_current_eod_holiday_context_and_invalid_profile(tmp_path):
    a=TestAdapter()
    ctx=BatchContext(requested_as_of='2026-09-07T12:00:00-04:00',requested_session='2026-09-04',effective_daily_session='2026-09-04',mode='CURRENT_EOD')
    assert spec(tmp_path,a,context=ctx).context==ctx
    with pytest.raises(ValueError,match='CONTEXT_MISMATCH'):
        spec(tmp_path,a,context=ctx.model_copy(update={'effective_daily_session':'2026-09-07'}))
    with pytest.raises(ValueError):SelectionProfile(profile_id='latest')


def test_revised_history_detects_content_change(tmp_path):
    a=TestAdapter(40);b=TestAdapter(43,changes={20:{'high':105.}})
    old=a.prepare('TEST',a.verify('TEST'));new=b.prepare('TEST',b.verify('TEST'))
    proof=compatible_prefix(old,new)
    assert not proof['compatible'] and proof['reason']=='HISTORICAL_DATA_REVISED'
    assert proof['earliest_changed_session']==str(a.input.feature_view.bars[20].session)


def test_only_failed_family_retried_and_sort_policy_rebuilds_downstream(tmp_path,monkeypatch):
    import pcs.pool.observation_components as components
    from pcs.selection.models import RankingPolicy
    calls=[];original=components.family_component
    def fail_once(*args,**kwargs):
        family=args[3];calls.append(family)
        if family=='CONSTRUCTIVE_BASE' and calls.count(family)==1:raise RuntimeError('TEST interrupted component')
        return original(*args,**kwargs)
    monkeypatch.setattr(components,'family_component',fail_once)
    a=TestAdapter();inp=spec(tmp_path,a,selection_profile=SelectionProfile(families=['HEALTHY_PULLBACK','CONSTRUCTIVE_BASE']))
    first=run(inp,a)
    assert first.status=='PARTIAL'
    second=run(inp.model_copy(update={'resume_run_id':inp.run_id}),a)
    assert calls.count('HEALTHY_PULLBACK')==1 and calls.count('CONSTRUCTIVE_BASE')==2
    changed=inp.selection_profile.model_copy(update={'ranking':RankingPolicy(liquidity_currency='EUR')})
    updated=run(inp.model_copy(update={'run_id':'ranking-change','previous_run':second.output_directory,'selection_profile':changed}),a)
    assert len(calls)==3 and updated.selection_v2['cache_hits']>=4


def test_deadline_and_revision_reject_late_completion(tmp_path):
    from pcs.pool.observation_models import ObservationSymbol
    store=ObservationStore(tmp_path,'spec','run');state=ObservationSymbol(symbol='TEST')
    with pytest.raises(ValueError,match='STALE_ATTEMPT'):
        store.commit(state,'PROFILE','dep',{},token=store.token,expected_revision=0,deadline=perf_counter()-1)
    state=store.commit(state,'PROFILE','dep',{},token=store.token,expected_revision=0,deadline=perf_counter()+5)
    with pytest.raises(ValueError,match='STALE_ATTEMPT'):
        store.commit(state,'PROFILE','dep',{},token=store.token,expected_revision=0,deadline=perf_counter()+5)


def test_partial_preparation_retains_profile(tmp_path):
    class ShortAdapter(TestAdapter):
        def profile_input(self,symbol,verified):
            return super().prepare(symbol,verified).profile_input
        def prepare(self,*args):raise ValueError('OPPORTUNITY_INDICATOR_WARMUP_INSUFFICIENT')
    adapter=ShortAdapter();result=run(spec(tmp_path,adapter),adapter)
    query=read_stock_observation(ObservationQuery(run_directory=result.output_directory,symbol='TEST'))
    assert 'PROFILE' in query.state.components and 'CONSTRUCTIVE_BASE' not in query.state.components
    assert query.packet.component_refs


def test_current_eod_new_request_reuses_facts_but_rechecks_qualification(tmp_path):
    import pandas as pd
    a=TestAdapter(43);day=a.input.call_context.effective_daily_session
    first_context=BatchContext(requested_as_of=day+'T16:30:00-05:00',requested_session=day,effective_daily_session=day,mode='CURRENT_EOD')
    inp=spec(tmp_path,a,context=first_context)
    first=run(inp,a)
    next_day=(pd.Timestamp(day)+pd.Timedelta(days=1)).date().isoformat()
    next_context=BatchContext(requested_as_of=next_day+'T12:00:00-05:00',requested_session=day,effective_daily_session=day,mode='CURRENT_EOD')
    second=run(inp.model_copy(update={'run_id':'weekend','context':next_context,'previous_run':first.output_directory}),a)
    assert a.counts['prepares']==1
    packet=read_stock_observation(ObservationQuery(run_directory=second.output_directory,symbol='TEST')).packet
    assert packet.context.requested_as_of==next_context.requested_as_of
    assert packet.context.requested_session==day
    assert packet.program_assessments[0]['eligible_at_requested_time'] is True


def test_family_policy_change_recomputes_only_affected_family(tmp_path,monkeypatch):
    import pcs.pool.observation_components as components
    from pcs.trend.selection_models import ConstructiveBasePolicy
    calls=[];original=components.family_component
    def trace(*args,**kwargs):calls.append(args[3]);return original(*args,**kwargs)
    monkeypatch.setattr(components,'family_component',trace)
    a=TestAdapter();inp=spec(tmp_path,a,selection_profile=SelectionProfile(families=['HEALTHY_PULLBACK','CONSTRUCTIVE_BASE']))
    first=run(inp,a)
    profile=inp.selection_profile.model_copy(update={'base':ConstructiveBasePolicy(maximum_width_atr=3.5)})
    run(inp.model_copy(update={'run_id':'policy','previous_run':first.output_directory,'selection_profile':profile}),a)
    assert calls.count('HEALTHY_PULLBACK')==1 and calls.count('CONSTRUCTIVE_BASE')==2
    assert a.counts['prepares']==1


def test_query_checks_symbol_index_and_real_rank_refs(tmp_path):
    from pcs.selection.models import EvidenceQuery
    from pcs.selection.packets import resolve_evidence
    a=TestAdapter();result=run(spec(tmp_path,a),a)
    root=Path(result.output_directory)
    row=read_json(root/'stock_shortlist.json')['rows'][0]
    query=read_stock_observation(ObservationQuery(run_directory=str(root),symbol='TEST'))
    refs={ref for key in row['sort_keys'] for ref in key['source_refs']}
    for ref in refs:
        assert resolve_evidence(EvidenceQuery(packet=query.packet,evidence_id=ref)).status=='RESOLVED'
    (root/'symbol_index.json').write_text('{}')
    with pytest.raises(ValueError,match='MANIFEST_HASH_MISMATCH'):
        read_stock_observation(ObservationQuery(run_directory=str(root),symbol='TEST'))


def test_cli_profile_and_child_spec_are_identical(tmp_path,monkeypatch):
    import pcs.pool.process as process
    from pcs.pool.observation_cli import run_observation_command
    from types import SimpleNamespace
    a=TestAdapter();inp=spec(tmp_path,a)
    path=tmp_path/'spec.json';path.write_text(inp.model_dump_json())
    def supervisor(request,**kwargs):
        assert request.observation_spec==inp.model_dump(mode='json')
        assert request.symbols==tuple(inp.symbols)
        return SimpleNamespace(run_id=inp.run_id,status='TEST',coverage='PARTIAL',output_directory=str(tmp_path),summary={},selection_v2={})
    monkeypatch.setattr(process,'run_read_only_scan',supervisor)
    args=SimpleNamespace(observation_spec=str(path),data_mode='READ_ONLY',auto_prepare_data=False,
        selection_profile=inp.selection_profile.profile_id,resume_run_id=None)
    run_observation_command(args)
    args.selection_profile='unknown'
    with pytest.raises(ValueError,match='PROFILE_MISMATCH'):run_observation_command(args)


def test_all_families_share_preparation_and_repeat_calls_zero(tmp_path):
    adapter=TestAdapter();inp=spec(tmp_path,adapter,selection_profile=SelectionProfile())
    first=run(inp,adapter)
    second=run(inp.model_copy(update={'resume_run_id':inp.run_id}),adapter)
    assert adapter.counts['prepares']==1 and adapter.counts['benchmark']==2
    for family in inp.selection_profile.families:
        assert first.selection_v2['component_counts'][family]['committed']==1
        assert second.selection_v2['component_counts'][family]['cache_hits']==1
        assert second.selection_v2['component_counts'][family].get('committed',0)==0


def test_generation_change_is_explicit_replay_not_checkpoint_rewrite(tmp_path):
    a=TestAdapter(40);first=run(spec(tmp_path,a),a)
    b=TestAdapter(43)
    view=b.input.feature_view
    source=view.source.model_copy(update={'record_identity':'TEST:new-generation','sha256':'TEST:new-checksum'})
    b.input=b.input.model_copy(update={'feature_view':view.model_copy(update={'source':source})})
    second=run(spec(tmp_path,b,run_id='new-generation',previous_run=first.output_directory),b)
    query=read_stock_observation(ObservationQuery(run_directory=second.output_directory,symbol='TEST'))
    assert query.state.lineage[-1]['prefix_equal'] is True
    assert query.state.lineage[-1]['reason']=='GENERATION_CHANGED_CORE_REQUIRES_REPLAY'
    cold=run(spec(tmp_path,b,run_id='cold-new-generation'),b)
    other=read_stock_observation(ObservationQuery(run_directory=cold.output_directory,symbol='TEST'))
    assert query.state.components['CONSTRUCTIVE_BASE'].result_id==other.state.components['CONSTRUCTIVE_BASE'].result_id


def test_interrupted_query_reads_only_committed_and_counts_current_attempt(tmp_path):
    from pcs.pool.observation_storage import interrupted_run
    a=TestAdapter();inp=spec(tmp_path,a,symbols=['TEST','MISSING'])
    a.blocked=['MISSING'];first=run(inp,a)
    root=Path(first.output_directory)
    (root/'observation_manifest.json').unlink()
    query=read_stock_observation(ObservationQuery(run_directory=str(root),symbol='TEST'))
    assert query.status=='RESOLVED' and query.reason_codes==['IN_PROGRESS_COMMITTED_EVIDENCE_ONLY']
    partial=interrupted_run(inp,'TEST_PROCESS_TIMEOUT','TEST')
    assert partial.summary['execution_completed_count']==1 and partial.summary['data_blocked_count']==1
    ObservationStore(root,first.spec_id,first.run_id,resume=True)
    retried=interrupted_run(inp,'TEST_PROCESS_TIMEOUT','TEST')
    assert retried.summary['unprocessed_count']==2
    assert not (tmp_path/'CURRENT.json').exists()
    continued=run(inp.model_copy(update={'run_id':'from-partial','previous_run':str(root)}),a)
    assert continued.selection_v2['cache_hits']>=4


def test_scope_removed_and_missing_are_not_invalidation(tmp_path):
    a=TestAdapter(blocked=['MISSING'])
    first=run(spec(tmp_path,a,symbols=['TEST','MISSING']),a)
    second=run(spec(tmp_path,a,run_id='removed',previous_run=first.output_directory),a)
    changes=read_json(Path(second.output_directory)/'shortlist_changes.json')
    removed=next(c for c in changes if c['symbol']=='MISSING')
    assert removed['reason_codes']==['SCOPE_REMOVED']
    packet=read_stock_observation(ObservationQuery(run_directory=second.output_directory,symbol='TEST')).packet
    assert packet.ai_review_status=='NOT_REVIEWED'
    assert packet.user_decision_status=='NOT_RECORDED'


def test_support_failure_preserves_independent_base_and_breakout(tmp_path,monkeypatch):
    import pcs.pool.observation_components as components
    def unavailable(*args,**kwargs):raise ValueError('TEST_SUPPORT_UNAVAILABLE')
    monkeypatch.setattr(components,'support_component',unavailable)
    adapter=TestAdapter()
    result=run(spec(tmp_path,adapter,selection_profile=SelectionProfile()),adapter)
    query=read_stock_observation(ObservationQuery(run_directory=result.output_directory,symbol='TEST'))
    assert result.status=='PARTIAL'
    assert {'PROFILE','CONSTRUCTIVE_BASE','BREAKOUT_RETEST','PACKET'}<=set(query.state.components)
    assert 'HEALTHY_PULLBACK' not in query.state.components


def _cpu_identity(value):
    import os
    return os.getpid(),value


def test_runtime_cpu_values_and_owned_worker_cleanup():
    import multiprocessing,os
    from pcs.pool.runtime import PoolRuntime
    runtime=PoolRuntime(max_workers=2)
    before={p.pid for p in multiprocessing.active_children()}
    try:
        result=runtime.run_cpu(_cpu_identity,{'TEST':'typed payload'},timeout_seconds=30)
        assert result[0]!=os.getpid() and result[1]=={'TEST':'typed payload'}
        assert len({p.pid for p in multiprocessing.active_children()}-before)==2
    finally:runtime.close()
    assert {p.pid for p in multiprocessing.active_children()}==before


def test_changed_code_dependency_invalidates_prior_state(tmp_path,monkeypatch):
    import pcs.pool.observation_components as components
    a=TestAdapter();first=run(spec(tmp_path,a),a)
    original=components.family_component;priors=[]
    def inspect_prior(prepared,support,profile,family,previous=None):
        priors.append(previous)
        if len(priors)==1:raise RuntimeError('TEST_INTERRUPT_NEW_CODE_COMPONENT')
        return original(prepared,support,profile,family,previous)
    monkeypatch.setattr(components,'family_component',inspect_prior)
    monkeypatch.setattr(components,'dependencies',lambda:{'TEST_SHARED_INDICATOR_DEPENDENCY':'changed'})
    inp=spec(tmp_path,a,run_id='code-change',previous_run=first.output_directory)
    second=run(inp,a)
    assert priors==[None]
    query=read_stock_observation(ObservationQuery(run_directory=second.output_directory,symbol='TEST'))
    assert query.state.lineage[-1]['reason']=='CODE_DEPENDENCY_CHANGED_REPLAY'
    run(inp.model_copy(update={'resume_run_id':inp.run_id}),a)
    assert priors==[None,None]


def test_real_preparation_retains_short_history_and_unknown_long_indicator():
    import exchange_calendars as xc
    from pcs.pool.opportunities import OpportunityDataReader
    from pcs.analysis_contracts import CallContext,SourceReference
    sessions=[str(s.date()) for s in xc.get_calendar('XNYS').sessions_in_range('2025-01-02','2026-09-04')][-170:]
    bars=[DailyBar(session=s,open=100+i*.1,high=102+i*.1,low=99+i*.1,close=101+i*.1,volume=1000000.) for i,s in enumerate(sessions)]
    view=DailyFeatureView(symbol='TEST',bars=bars,source=SourceReference(source_id='TEST:short',source_kind='TEST',validated=True),
        price_basis='TEST',corporate_action_version='TEST',input_kind='TEST')
    context=CallContext(symbol='TEST',requested_as_of=sessions[-1],effective_daily_session=sessions[-1],mode='HISTORICAL',run_id='TEST',request_id='TEST')
    reader=object.__new__(OpportunityDataReader);reader.audit=[]
    result=reader.prepare(context,daily=view,defer_support=True,allow_partial=True)
    assert len(result.feature_view.bars)==170
    assert result.feature_view.bars[-1].sma20 is not None
    assert result.feature_view.bars[-1].sma200 is None
    assert result.feature_view.bars[-1].trend_health is None
    assert len(reader.prepared_support_view.bars)==60
    from pcs.pool.observation_components import SupportObservation,family_component
    prepared=PreparedObservation(opportunity=result,support_view=reader.prepared_support_view,
        profile_input=ProfileInput(call_context=context,feature_view=view),data_identity='TEST:170',audit={'TEST':True})
    for family in SelectionProfile().families:
        opportunity=family_component(prepared,SupportObservation(),SelectionProfile(),family)
        assert opportunity.family==family
        assert opportunity.eligible_at_requested_time is not True


def test_partial_indicator_opt_in_preserves_full_input_values_and_legacy_rejection():
    import pandas as pd
    from pcs.trend.indicators import calculate_base_indicators
    from pcs.trend.models import TrendIndicatorValidationError
    fixture=runpy.run_path(str(Path(__file__).parents[1]/'trend/test_indicators.py'))
    data=fixture['make_ohlcv'](260)
    pd.testing.assert_frame_equal(calculate_base_indicators(data),calculate_base_indicators(data,allow_partial_warmup=True))
    with pytest.raises(TrendIndicatorValidationError):calculate_base_indicators(data.iloc[:170])
    partial=calculate_base_indicators(data.iloc[:170],allow_partial_warmup=True)
    assert partial.sma200.isna().all() and partial.sma20.notna().any()


def test_output_failure_and_not_started_have_distinct_counts(tmp_path,monkeypatch):
    import pcs.selection.packets as packets
    def fail(_):raise ValueError('TEST_OUTPUT_FAILURE')
    monkeypatch.setattr(packets,'build_decision_evidence_packet',fail)
    adapter=TestAdapter();result=run(spec(tmp_path,adapter),adapter)
    assert result.summary['failed_count']==1 and result.summary['timed_out_count']==0
    assert not result.current_published
    limited=run(spec(tmp_path,adapter,run_id='not-started',budgets=ObservationBudgets(total_seconds=.001)),adapter)
    assert limited.summary['unprocessed_count']==1 and limited.summary['timed_out_count']==0
    assert limited.status=='PARTIAL' and not limited.current_published


def test_dependency_changed_during_run_cannot_publish(tmp_path,monkeypatch):
    import pcs.pool.observation_components as components
    original=components.dependencies;calls=[]
    def changing():
        calls.append(True)
        return original() if len(calls)==1 else {'TEST':'changed during run'}
    monkeypatch.setattr(components,'dependencies',changing)
    adapter=TestAdapter()
    with pytest.raises(ValueError,match='CODE_CHANGED_DURING_RUN'):
        run(spec(tmp_path,adapter),adapter)
    assert not (tmp_path/'CURRENT.json').exists()


def test_later_component_timeout_is_not_hidden_by_earlier_failure(tmp_path,monkeypatch):
    import pcs.pool.observation_components as components
    from pcs.pool.runtime import PoolRuntime,StageRun
    from pcs.pool.concurrency import WorkerOutcome
    original=PoolRuntime.run_stage
    def stage(runtime,symbols,worker,**kwargs):
        if kwargs.get('stage_name')=='CONSTRUCTIVE_BASE':
            return StageRun(tuple(WorkerOutcome(s,reason_codes=('WORKER_TIMEOUT',)) for s in symbols),1.)
        return original(runtime,symbols,worker,**kwargs)
    def fail(*args,**kwargs):raise ValueError('TEST_EARLIER_FAMILY_FAILURE')
    monkeypatch.setattr(PoolRuntime,'run_stage',stage)
    monkeypatch.setattr(components,'family_component',fail)
    adapter=TestAdapter();result=run(spec(tmp_path,adapter,
        selection_profile=SelectionProfile(families=['HEALTHY_PULLBACK','CONSTRUCTIVE_BASE'])),adapter)
    query=read_stock_observation(ObservationQuery(run_directory=result.output_directory,symbol='TEST'))
    assert result.summary['timed_out_count']==1
    assert query.state.component_failures['CONSTRUCTIVE_BASE']==['WORKER_TIMEOUT']
    assert 'TEST_EARLIER_FAMILY_FAILURE' in query.state.reason_codes
