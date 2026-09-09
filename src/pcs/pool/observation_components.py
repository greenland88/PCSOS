"""Component preparation, dependency proofs and accepted API wiring. No scheduler."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from threading import RLock

from pydantic import Field
from pcs.analysis_contracts import CallContext,StrictModel
from pcs.selection.identity import digest as _digest,semantic
from pcs.selection.models import RankingInput,Diagnostic,InputFailure
from pcs.trend.selection_models import (OpportunityInput,SupportFeatureView,ProfileInput,
    SupportZoneInput,SupportZoneResult,OpportunitySupportFact,EntryOpportunity,UnderlyingProfile,DailyFeatureView)
from pcs.pool.opportunities import OpportunityDataReader,evaluate_pool_opportunity_observation
from pcs.pool.underlying_profiles import ProfileDataReader
from pcs.trend.support_zones import evaluate_support_zones
from pcs.trend.underlying_profile import measure_underlying_profile


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    def plain(item):
        if hasattr(item,'model_dump'):return item.model_dump(mode='json')
        if isinstance(item,(tuple,list)):return [plain(v) for v in item]
        if isinstance(item,dict):return {k:plain(v) for k,v in item.items()}
        return item
    return _digest(plain(value))


def dependencies():
    root=Path(__file__).resolve().parents[3]
    # Conservative indirect engine/data dependency closure. Rendering/ranking excluded.
    paths=[root/'src/pcs/analysis_contracts.py',Path(__file__),root/'src/pcs/pool/opportunities.py',
        root/'src/pcs/pool/underlying_profiles.py',root/'src/pcs/pool/observation_models.py',
        root/'src/pcs/pool/runner.py',root/'src/pcs/pool/runtime.py',root/'src/pcs/pool/modes.py']
    for folder in ('trend','entry','data','models','config'):
        paths.extend((root/'src/pcs'/folder).rglob('*.py'))
    return {str(p.relative_to(root)):sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths)) if p.is_file()}


class PreparedObservation(StrictModel):
    opportunity: OpportunityInput
    support_view: SupportFeatureView
    profile_input: ProfileInput
    data_identity: str
    audit: dict


class SupportObservation(StrictModel):
    result: SupportZoneResult | None = None
    facts: list[OpportunitySupportFact] = Field(default_factory=list)
    result_ids: dict[str,str] = Field(default_factory=dict)


class CanonicalObservationAdapter:
    def __init__(self,spec,runtime):
        self.spec,self.runtime=spec,runtime
        self.reader=ProfileDataReader(runtime.access)
        self.reader.snapshot=runtime.manifest_snapshot
        self.benchmark=None
        self.benchmark_error=None
        self.counts={'canonical_reads':0,'benchmark_reads':0,'base_indicator_calculations':0,
            'prepared_symbols':0,'provider_calls':0,'options_calls':0,'verification_attempts':0,
            'explicit_source_hash_reads':0}
        self.windows={}
        self.read_lock=RLock()

    def _daily(self,symbol,seed=None):
        import exchange_calendars as xc
        import pandas as pd
        profile=self.spec.selection_profile
        self.counts['verification_attempts']+=1
        day=self.spec.context.effective_daily_session
        required=max(profile.profile.required_sessions,profile.support.required_sessions,profile.opportunity.required_sessions)
        if seed:
            required=max(required,len(xc.get_calendar(self.spec.context.calendar).sessions_in_range(seed,day)))
        with self.read_lock:
            before=len(self.reader.audit)
            value=self.reader._read(symbol,day,SimpleNamespace(required_sessions=required),self.spec.context.calendar)
            actual=len(self.reader.audit)-before
            self.counts['canonical_reads']+=actual
            self.counts['explicit_source_hash_reads']+=actual*len(value.source.detail.get('canonical_paths',[]))
        self.windows[symbol]=dict(required_sessions=required,actual_sessions=len(value.bars),
            seed=str(value.bars[0].session) if value.bars else None)
        # Logical read bounds are invocation audit, not changing physical source identity.
        detail={k:v for k,v in value.source.detail.items() if k not in ('logical_read_start','logical_read_end')}
        return value.model_copy(update={'source':value.source.model_copy(update={'detail':detail})})

    def load_benchmark(self,seed=None):
        try:
            benchmark=self._daily('SPY',seed)
            self.counts['benchmark_reads']+=1
            return benchmark,None
        except (ValueError,RuntimeError) as exc:
            return None,str(exc)

    def verify(self,symbol,previous=None):
        seed=previous.opportunity.feature_view.indicator_seed_start if previous else None
        daily=self._daily(symbol,seed)
        identity=digest([daily.model_dump(mode='json'),self.benchmark.model_dump(mode='json') if self.benchmark else None])
        return daily,identity

    def profile_input(self,symbol,verified):
        context=CallContext(symbol=symbol,requested_as_of=self.spec.context.requested_as_of,
            effective_daily_session=self.spec.context.effective_daily_session,mode=self.spec.context.mode,
            run_id=self.spec.run_id,request_id=self.spec.context.request_id,scope='STOCK_OBSERVATION')
        return ProfileInput(call_context=context,feature_view=verified[0],benchmark=self.benchmark,
            benchmark_reason_codes=['BENCHMARK_UNAVAILABLE',self.benchmark_error] if self.benchmark_error else [],
            effective_policy=self.spec.selection_profile.profile,calendar=self.spec.context.calendar)

    def preparation_identity(self,previous=None):
        import exchange_calendars as xc
        import pandas as pd
        cal=xc.get_calendar(self.spec.context.calendar)
        end=cal.sessions.get_loc(pd.Timestamp(self.spec.context.effective_daily_session))
        return dict(calendar=self.spec.context.calendar,
            analysis_start=previous.opportunity.feature_view.analysis_start if previous else
                str(cal.sessions[end-self.spec.selection_profile.opportunity.analysis_sessions+1].date()),
            support_start=previous.support_view.analysis_start if previous else
                str(cal.sessions[end-self.spec.selection_profile.support.analysis_sessions+1].date()))

    def prepare(self,symbol,verified,previous=None):
        daily,identity=verified
        profile=self.spec.selection_profile
        context=CallContext(symbol=symbol,requested_as_of=self.spec.context.requested_as_of,
            effective_daily_session=self.spec.context.effective_daily_session,mode=self.spec.context.mode,
            run_id=self.spec.run_id,request_id=self.spec.context.request_id,scope='STOCK_OBSERVATION')
        reader=object.__new__(OpportunityDataReader);reader.audit=[]
        anchor=previous.opportunity.feature_view.analysis_start if previous else None
        import exchange_calendars as xc
        import pandas as pd
        cal=xc.get_calendar(self.spec.context.calendar)
        end=cal.sessions.get_loc(pd.Timestamp(self.spec.context.effective_daily_session))
        support_start=previous.support_view.analysis_start if previous else str(cal.sessions[end-profile.support.analysis_sessions+1].date())
        opportunity=reader.prepare(context,daily=daily,benchmark=self.benchmark,policy=profile.opportunity,
            calendar=self.spec.context.calendar,analysis_start=anchor,defer_support=True,benchmark_error=self.benchmark_error,
            support_analysis_start=support_start,allow_partial=True)
        self.counts['base_indicator_calculations']+=1
        self.counts['prepared_symbols']+=1
        # Keep the entire frozen seed window for valid continuation.
        start=min(opportunity.feature_view.expected_sessions[0],str(daily.bars[0].session))
        if previous:
            start=min(start,previous.opportunity.feature_view.expected_sessions[0])
        end=opportunity.feature_view.expected_sessions[-1]
        expected=[str(s.date()) for s in cal.sessions_in_range(start,end)]
        opportunity=opportunity.model_copy(update={'feature_view':opportunity.feature_view.model_copy(update={'expected_sessions':expected})})
        return PreparedObservation(opportunity=opportunity,support_view=reader.prepared_support_view,
            profile_input=ProfileInput(call_context=context,feature_view=daily,benchmark=self.benchmark,
                benchmark_reason_codes=['BENCHMARK_UNAVAILABLE',self.benchmark_error] if self.benchmark_error else [],
                effective_policy=profile.profile,calendar=self.spec.context.calendar),data_identity=identity,
            audit={'preparation':reader.audit,'window':self.windows[symbol],
                'profile_statistics':'COMPONENT_SPECIFIC_WINDOW_AGGREGATION',
                'base_indicators':'ONE_FULL_FROZEN_SEED_COMPUTATION_PER_CHANGED_SYMBOL'})

    def release(self,symbols):
        for key in list(self.reader.cache):
            if key[0] in symbols and key[0]!='SPY':
                self.reader.cache.pop(key,None)

    def verify_unchanged(self):
        self.counts['explicit_source_hash_reads']+=len(self.reader.file_hashes)
        return self.reader.verify_unchanged()


def support_component(prepared,profile,prior=None):
    view=prepared.support_view
    context=prepared.opportunity.call_context
    if prior and prior.result and prior.result.effective_policy!=profile.support:
        prior=None
    state=prior.result.next_state if prior and prior.result else None
    facts=list(prior.facts) if prior else []
    ids=dict(prior.result_ids) if prior else {}
    last=prior.result if prior else None
    for session in view.expected_sessions:
        if session>context.effective_daily_session or session<view.analysis_start or (state and state.evaluated_through and session<=state.evaluated_through):
            continue
        result=evaluate_support_zones(SupportZoneInput(call_context=context.model_copy(update={
            'effective_daily_session':session,'requested_as_of':session,'mode':'HISTORICAL'}),
            feature_view=view,effective_policy=profile.support,prior_state=state))
        state=result.next_state;last=result;ids[session]=result.result_id
        for zone in result.current_zones+result.archived_zones:
            for test in zone.tests:
                if test.touch_session<=session:
                    facts.append(OpportunitySupportFact(session=session,support_result_id=result.result_id,
                        zone_id=zone.zone_id,test_id=test.test_id,zone_lower=zone.lower,zone_upper=zone.upper,
                        anchor_atr=zone.anchor_atr,invalidation_line=zone.invalidation_line,zone_available_at=zone.available_at,
                        touch_session=test.touch_session,test_status=test.status,first_held_at=test.first_held_at,
                        broken_at=zone.broken_at,zone_state=zone.state,source_ids=list(zone.observed_source_ids),
                        sources=list(zone.observed_sources or zone.creation_sources),reason_codes=list(dict.fromkeys(zone.reason_codes+test.reason_codes))))
    return SupportObservation(result=last,facts=facts,result_ids=ids)


def family_component(prepared,support,profile,family,previous=None):
    if previous:
        if family=='CONSTRUCTIVE_BASE' and previous.base_result.effective_policy!=profile.base:
            previous=None
        elif family=='BREAKOUT_RETEST' and previous.breakout_result.effective_policy!=profile.breakout:
            previous=None
    inp=prepared.opportunity.model_copy(update={'enabled_families':[family],
        'effective_policy':profile.opportunity,'shallow_policy':profile.shallow,'breakout_policy':profile.breakout,
        'base_policy':profile.base,'support_policy':profile.support,'support_facts':support.facts,'support_result_ids':support.result_ids,
        'prior_family_results':[previous] if previous else []})
    try:
        return evaluate_pool_opportunity_observation(inp)
    except ValueError as exc:
        if previous and str(exc) in ('BASE_PRIOR_REPLAY_REQUIRED','BREAKOUT_PRIOR_REPLAY_REQUIRED'):
            result=evaluate_pool_opportunity_observation(inp.model_copy(update={'prior_family_results':[]}))
            return result.model_copy(update={'call_diagnostics':result.call_diagnostics+['OBSERVATION_COMPONENT_POLICY_REPLAY',str(exc)]})
        raise


def compatible_prefix(old,new):
    """Compare full consumed content, including holes, instead of dates/row counts."""
    before=old.opportunity.feature_view
    after=new.opportunity.feature_view
    through=old.opportunity.call_context.effective_daily_session
    prior=[b.model_dump(mode='json') for b in before.bars if str(b.session)<=through]
    current=[b.model_dump(mode='json') for b in after.bars if str(b.session)<=through]
    old_days={b['session']:b for b in prior};new_days={b['session']:b for b in current}
    changed=next((s for s in sorted(set(old_days)|set(new_days)) if old_days.get(s)!=new_days.get(s)),None)
    equal=prior==current
    same_source=(before.source==after.source and before.auxiliary_sources==after.auxiliary_sources and
        before.indicator_identity==after.indicator_identity and before.price_basis==after.price_basis and
        before.corporate_action_version==after.corporate_action_version)
    return dict(prefix_equal=equal,compatible=equal and same_source,earliest_changed_session=changed,
        old_prefix=digest(prior),new_prefix=digest(current),source_identity_unchanged=same_source,
        additional_sessions=[str(b.session) for b in after.bars if str(b.session)>through],
        prior_evidence_session=through,new_evidence_session=new.opportunity.call_context.effective_daily_session,
        reason='APPEND_PREFIX_VERIFIED' if equal and same_source else
            'GENERATION_CHANGED_CORE_REQUIRES_REPLAY' if equal else 'HISTORICAL_DATA_REVISED')


def ranking_input(spec,symbol,components,failures=()):
    profile=components.get('PROFILE')
    support=components.get('SUPPORT')
    results=[components[f] for f in spec.selection_profile.families if f in components]
    diagnostics=[]
    if profile:
        for metric in profile.measurements:
            if metric.metric_id=='dollar_volume_median_20':
                diagnostics.append(Diagnostic(symbol=symbol,result_id=profile.result_id,metric_id=metric.metric_id,
                    as_of=profile.as_of,value=metric.value,unit=metric.unit,currency='USD',
                    price_basis=profile.provenance[0].detail.get('price_basis'),validated=metric.status=='COMPLETED',
                    source_refs=metric.evidence_refs,record=metric.model_dump(mode='json')))
    return RankingInput(context=spec.context,requested_symbols=spec.symbols,enabled_families=spec.selection_profile.families,
        opportunities=results,policy=spec.selection_profile.ranking,diagnostics=diagnostics,
        supports=[support.result] if support and support.result else [],
        failures=[InputFailure(symbol=symbol,stage='OBSERVATION',reason_codes=list(failures))] if failures else [])


def profile_records(profile):
    from pcs.selection.packets import evidence_record
    if profile is None:return []
    records=[]
    for field,kind in [('measurements','METRIC'),('episodes','DRAWDOWN_EPISODE')]:
        for i,value in enumerate(getattr(profile,field)):
            entity=getattr(value,'metric_id',getattr(value,'episode_id',field+str(i)))
            records.append(evidence_record(profile.result_id,f'/{field}/{i}',entity,profile.as_of,profile.version,
                'OBSERVATION_PROFILE',kind,value))
    records.append(evidence_record(profile.result_id,'/coverage','coverage:'+profile.result_id,profile.as_of,
        profile.version,'OBSERVATION_PROFILE','PROFILE_COVERAGE',profile.coverage))
    return records
