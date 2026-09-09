"""U1 ticker-pool runner: raw universe, static data readiness, and daily timing."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from time import perf_counter
from dataclasses import asdict, dataclass, replace, is_dataclass
from typing import Any, Literal, Mapping, Sequence
import uuid
import os
import traceback
import json
import hashlib
from threading import RLock
from pathlib import Path
from math import isfinite
from types import SimpleNamespace

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.strategy_readiness import (resolve_active_verified_daily_handle,
                                          resolve_active_verified_options_handle)
from pcs.data.control_plane import MarketDataRequirements, ensure_market_data
from pcs.data.canonical_generations import admit_migrated_daily_symbol
from pcs.trend.snapshot import build_trend_snapshot
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.interpretation import interpret_trend
from pcs.trend.scoring import score_trend
from pcs.entry.trend_gate import evaluate_trend_gate
from pcs.entry.pullback_gate import evaluate_pullback_gate
from .models import (EligibilityStatus, FinalAction, OptionsStatus, PoolRunSnapshot,
                     PoolScanResult, TickerScanResult, TimingStatus)
from .registry import UniverseSpec, evaluate_static_eligibility, resolve_pool_universe
from .modes import resolve_effective_market_session
from .runtime import PoolRuntime
from .concurrency import run_symbol_workers
from .options import discover_spreads, load_pool_option_rules

_PREPARATION_LOCK = RLock()


def _run_stock_observation(spec, *, data_access=None, adapter=None, checkpoint_callback=None):
    """Observation scope of this runner, before any legacy eligibility/timing gates.

    Workers return values only. This thread owns every durable component commit.
    Histories are loaded for at most max_workers symbols and then released.
    """
    from collections import Counter
    from threading import Event,Thread
    import sys
    from pcs.selection.identity import digest,semantic
    from pcs.selection.models import RankingInput,DecisionPacketInput,StockRow,ResultBinding,StockShortlist,DecisionEvidencePacket,GROUPS
    from pcs.selection.ranking import build_row,rank_prepared_rows
    from pcs.selection.packets import build_decision_evidence_packet,resolve_evidence
    from pcs.selection.models import EvidenceQuery
    from pcs.selection.storage import shortlist_csv,shortlist_markdown
    from .observation_models import StockObservationInput,StockObservationRun,ObservationSymbol
    from .observation_storage import ObservationStore,read_json,read_ref,write_json
    from .observation_components import (CanonicalObservationAdapter,PreparedObservation,SupportObservation,
        dependencies,now,support_component,family_component,ranking_input,compatible_prefix,
        measure_underlying_profile,EntryOpportunity,UnderlyingProfile,digest,profile_records)
    spec=StockObservationInput.model_validate(spec)
    if adapter is not None and getattr(adapter,'input_kind',None)!='TEST':
        raise ValueError('OBSERVATION_CUSTOM_ADAPTER_TEST_ONLY')
    profile=spec.selection_profile
    started=perf_counter();deadline=started+spec.budgets.total_seconds
    print(json.dumps(dict(status='POOL_SCAN_STARTED',scope=spec.scope,run_id=spec.run_id,total=len(spec.symbols))),file=sys.stderr,flush=True)
    code=Path(__file__).resolve().parents[3]
    source_commit=__import__('subprocess').check_output(['git','rev-parse','HEAD'],cwd=code,text=True).strip()
    closure=dependencies();code_id=digest(closure)
    config_snapshot={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in
        (Path('config/data_source_routes.yaml'),Path('config/market_data_source_registry.yaml'),
         Path('config/data_remediation_registry.yaml'),Path('config/data/corporate_actions.csv')) if p.is_file()}
    code_id=digest([code_id,config_snapshot])
    payload=spec.model_dump(mode='json',exclude={'resume_run_id','run_id'})
    spec_id=digest([payload,source_commit,code_id])
    root=Path(spec.output_directory)/spec.run_id
    if spec.universe_source:
        if hashlib.sha256(Path(spec.universe_source).read_bytes()).hexdigest()!=spec.universe_sha256:
            raise ValueError('OBSERVATION_UNIVERSE_HASH_MISMATCH')
        universe=read_json(spec.universe_source)
        members=universe.get(spec.universe_members_field,[])
        if sorted(set(members))!=spec.symbols:
            raise ValueError('OBSERVATION_UNIVERSE_MEMBERSHIP_MISMATCH')
    with PCSDataAccess._file_lock(root/'writer',blocking=False):
        store=ObservationStore(root,spec_id,spec.run_id,resume=bool(spec.resume_run_id))
        if checkpoint_callback:
            checkpoint_callback(str(store.path),spec_id)
        write_json(root/'observation_spec.json',spec.model_dump(mode='json'))
        write_json(root/'dependency_manifest.json',dict(source_commit=source_commit,code_id=code_id,sha256=closure,config_snapshot=config_snapshot))
        write_json(root/'universe.json',dict(universe_id=spec.universe_id,symbols=spec.symbols,membership_sha256=digest(spec.symbols),source=spec.universe_source,source_sha256=spec.universe_sha256))
        progress={'stage':'INITIALIZING','completed':0,'total':len(spec.symbols),'cache_hits':0,
            'execution_completed_count':0,'data_blocked_count':0,'failed_count':0,'timed_out_count':0,'unprocessed_count':len(spec.symbols)}
        stage_counts={}
        stop=Event()
        def emit():
            record=dict(status='POOL_SCAN_PROGRESS',scope=spec.scope,run_id=spec.run_id,attempt_id=store.token,
                elapsed_seconds=round(perf_counter()-started,3),checkpoint=str(store.path),**progress)
            print(json.dumps(record),file=sys.stderr,flush=True)
            write_json(root/'progress.json',record)
        def heartbeat():
            while not stop.wait(20): emit()
        thread=Thread(target=heartbeat,daemon=True);thread.start()
        try:
            saved=None;global_errors=[]
            if spec.saved_selection_manifest:
                from pcs.selection.adapters import load_selection_input
                saved=load_selection_input(spec.saved_selection_manifest)
                if semantic(saved.ranking_input.context)!=semantic(spec.context) or saved.ranking_input.requested_symbols!=spec.symbols:
                    raise ValueError('OBSERVATION_SAVED_SCOPE_OR_TIME_MISMATCH')
            access=None if saved or adapter else data_access or PCSDataAccess.canonical(manifest_path=spec.manifest_path,parquet_root=spec.parquet_root)
            runtime=PoolRuntime(access=access,run_id=spec.run_id,as_of=spec.context.requested_as_of,
                max_workers=spec.max_workers,stage_timeout_seconds=spec.budgets.component_seconds)
            if not saved and adapter is None:
                adapter=CanonicalObservationAdapter(spec,runtime)
            previous_index={};previous_root=None;previous_shortlist=None;previous_profile=None
            if spec.previous_run:
                previous_root=Path(spec.previous_run)
                manifest=read_json(previous_root/'observation_manifest.json')
                for name in ('symbol_index.json','stock_shortlist.json','observation_spec.json'):
                    if hashlib.sha256((previous_root/name).read_bytes()).hexdigest()!=manifest['sha256'][name]:
                        raise ValueError('OBSERVATION_PREVIOUS_HASH_MISMATCH')
                previous_index=read_json(previous_root/'symbol_index.json')
                previous_profile=read_json(previous_root/'observation_spec.json')['selection_profile']
                previous_shortlist=StockShortlist.model_validate(read_json(previous_root/'stock_shortlist.json'))
            if not saved:
                progress['stage']='BENCHMARK_VERIFICATION';emit()
                seeds=[]
                if isinstance(adapter,CanonicalObservationAdapter):
                    for origin,index in ((root,store.checkpoint['symbols']),(previous_root,previous_index)):
                        for symbol,ref in index.items():
                            if symbol in spec.symbols:
                                seed=read_ref(origin,ref).get('input_seed_start')
                                if seed:seeds.append(seed)
                def benchmark_worker(_):
                    return adapter.load_benchmark(min(seeds) if seeds else None) if isinstance(adapter,CanonicalObservationAdapter) else adapter.load_benchmark()
                run=runtime.run_stage(['SPY'],benchmark_worker,timeout_seconds=min(spec.budgets.verification_seconds,max(.001,deadline-perf_counter())))
                if run.outcomes[0].reason_codes:
                    global_errors+=list(run.outcomes[0].reason_codes)
                    if isinstance(adapter,CanonicalObservationAdapter):
                        adapter.benchmark_error='BENCHMARK_VERIFICATION_TIMEOUT_OR_FAILED'
                elif isinstance(adapter,CanonicalObservationAdapter):
                    adapter.benchmark,adapter.benchmark_error=run.outcomes[0].value
            pairs=[];packet_refs={};query_counts={};family_counts={};states_summary={};lineage_by_symbol={}
            for offset in range(0,len(spec.symbols),spec.max_workers):
                symbols=spec.symbols[offset:offset+spec.max_workers]
                states={s:store.state(s) for s in symbols}
                prior_states={s:ObservationSymbol.model_validate(read_ref(previous_root,previous_index[s]))
                    for s in symbols if s in previous_index}
                originals={};prepared={};values={s:{} for s in symbols};verified={};proofs={}
                blocked=set();errors={s:[] for s in symbols}
                def old_value(s,kind,model):
                    state=states[s] if kind in states[s].components else prior_states.get(s)
                    source=root if kind in states[s].components else previous_root
                    if state and kind in state.components:
                        return model.model_validate(read_ref(source,state.components[kind]))
                    return None
                for s in symbols:
                    if not saved:
                        originals[s]=old_value(s,'PREPARED',PreparedObservation)
                def execute(stage,worker,budget,targets,dependencies_by_symbol=None):
                    if not targets:return
                    stats=stage_counts.setdefault(stage,Counter())
                    progress['stage']=stage;emit()
                    limit=min(deadline,perf_counter()+budget)
                    runnable=[]
                    for s in targets:
                        if perf_counter()>=deadline:
                            errors[s].append('GLOBAL_DEADLINE_NOT_STARTED');continue
                        dep=dependencies_by_symbol.get(s) if dependencies_by_symbol else None
                        old=states[s].components.get(stage)
                        other=prior_states.get(s)
                        old=old or (other.components.get(stage) if other else None)
                        if dep and old and old.dependency_id==dep:
                            origin=root if stage in states[s].components else previous_root
                            values[s][stage]=read_ref(origin,old)
                            if origin!=root:
                                copied=store.put(s,stage,dep,values[s][stage],old.revision)
                                copied=copied.model_copy(update={'computed_at':old.computed_at})
                            else: copied=old
                            states[s]=states[s].model_copy(update={'components':{**states[s].components,stage:copied},
                                'served_at':now(),'cache_hits':states[s].cache_hits+1,
                                'component_failures':{k:v for k,v in states[s].component_failures.items() if k!=stage}})
                            store.save_state(states[s]);progress['cache_hits']+=1
                            stats['cache_hits']+=1
                        else:runnable.append(s)
                    if not runnable:return
                    expected={s:states[s].components[stage].revision if stage in states[s].components else 0 for s in runnable}
                    def commit(outcome):
                        s=outcome.symbol
                        if outcome.reason_codes:
                            stats['failed_or_timed_out']+=1
                            states[s]=states[s].model_copy(update={'component_failures':{**states[s].component_failures,stage:list(outcome.reason_codes)}})
                            errors[s]+=list(outcome.reason_codes);return
                        value=outcome.value
                        if dependencies_by_symbol:
                            dep=dependencies_by_symbol[s]
                            try:
                                states[s]=store.commit(states[s],stage,dep,value,token=store.token,
                                    expected_revision=expected[s],deadline=limit)
                            except ValueError as exc:
                                errors[s].append(str(exc));return
                        values[s][stage]=value
                        states[s]=states[s].model_copy(update={'component_failures':{k:v for k,v in states[s].component_failures.items() if k!=stage}})
                        stats['committed' if dependencies_by_symbol else 'verified']+=1
                    run=runtime.run_stage(runnable,worker,stage_name=stage,timeout_seconds=max(.001,limit-perf_counter()),on_outcome=commit)
                    stats['elapsed_ms']+=run.elapsed_ms
                    for outcome in run.outcomes:
                        if outcome.reason_codes and not errors[outcome.symbol]:errors[outcome.symbol]+=list(outcome.reason_codes)
                if not saved:
                    execute('VERIFY',lambda s:adapter.verify(s,originals[s]),spec.budgets.verification_seconds,symbols)
                    for s in symbols:
                        if 'VERIFY' in values[s]:verified[s]=values[s]['VERIFY']
                        else:blocked.add(s)
                    if hasattr(adapter,'profile_input'):
                        profile_deps={s:digest([code_id,verified[s][1],profile.profile,spec.context.effective_daily_session]) for s in verified}
                        execute('PROFILE',lambda s:measure_underlying_profile(adapter.profile_input(s,verified[s])),
                            spec.budgets.component_seconds,list(verified),profile_deps)
                    prep_deps={s:digest([code_id,verified[s][1],profile.opportunity,profile.support,
                        spec.context.effective_daily_session,
                        adapter.preparation_identity(originals[s]) if hasattr(adapter,'preparation_identity') else None]) for s in verified}
                    execute('PREPARED',lambda s:adapter.prepare(s,verified[s],originals[s]),spec.budgets.preparation_seconds,list(verified),prep_deps)
                    for s in verified:
                        if 'PREPARED' not in values[s]:blocked.add(s);continue
                        obj=PreparedObservation.model_validate(values[s]['PREPARED'])
                        context=obj.opportunity.call_context.model_copy(update={'requested_as_of':spec.context.requested_as_of,
                            'effective_daily_session':spec.context.effective_daily_session,'mode':spec.context.mode,
                            'run_id':spec.run_id,'request_id':spec.context.request_id})
                        prepared[s]=obj.model_copy(update={'opportunity':obj.opportunity.model_copy(update={'call_context':context})})
                        proofs[s]=compatible_prefix(originals[s],prepared[s]) if originals[s] else {'compatible':False,'reason':'COLD_START'}
                        states[s]=states[s].model_copy(update={'lineage':states[s].lineage+[proofs[s]],
                            'input_seed_start':obj.opportunity.feature_view.indicator_seed_start})
                    eligible=list(prepared)
                    support_deps={s:digest([code_id,prep_deps[s],profile.support]) for s in eligible}
                    execute('SUPPORT',lambda s:support_component(prepared[s],profile,
                        old_value(s,'SUPPORT',SupportObservation) if proofs[s]['compatible'] else None),
                        spec.budgets.component_seconds,eligible,support_deps)
                    profile_deps={s:digest([code_id,verified[s][1],profile.profile,spec.context.effective_daily_session]) for s in eligible}
                    if not hasattr(adapter,'profile_input'):
                        execute('PROFILE',lambda s:measure_underlying_profile(prepared[s].profile_input),spec.budgets.component_seconds,eligible,profile_deps)
                    for family in profile.families:
                        policies=[profile.opportunity,profile.shallow if family=='SHALLOW_PULLBACK' else
                            profile.breakout if family=='BREAKOUT_RETEST' else profile.base if family=='CONSTRUCTIVE_BASE' else None]
                        targets=[s for s in eligible if 'SUPPORT' in values[s]]
                        deps={s:digest([code_id,prep_deps[s],support_deps[s],policies,semantic(spec.context)]) for s in targets}
                        def evaluate(s,f=family):
                            prior=old_value(s,f,EntryOpportunity) if proofs[s]['compatible'] else None
                            # Core compatibility checks remain authoritative; no checkpoint fields are rewritten.
                            return family_component(prepared[s],SupportObservation.model_validate(values[s]['SUPPORT']),profile,f,prior)
                        execute(family,evaluate,spec.budgets.component_seconds,targets,deps)
                for s in symbols:
                    if saved:
                        inp=saved.ranking_input.model_copy(update={'policy':profile.ranking})
                        extra=saved.extra_records.get(s,[])
                        has_results=any(r.symbol==s for r in inp.opportunities)
                        execution='COMPLETED' if has_results else 'DATA_BLOCKED'
                        reasons=[c for f in inp.failures if f.symbol==s for c in f.reason_codes]
                    else:
                        components={f:EntryOpportunity.model_validate(values[s][f]) for f in profile.families if f in values[s]}
                        if 'SUPPORT' in values[s]:components['SUPPORT']=SupportObservation.model_validate(values[s]['SUPPORT'])
                        if 'PROFILE' in values[s]:components['PROFILE']=UnderlyingProfile.model_validate(values[s]['PROFILE'])
                        inp=ranking_input(spec,s,components,errors[s]);extra=profile_records(components.get('PROFILE'))
                        reasons=errors[s]
                        execution=('UNPROCESSED' if 'GLOBAL_DEADLINE_NOT_STARTED' in reasons or 'STAGE_DEADLINE_NOT_STARTED' in reasons else
                            'TIMED_OUT' if 'WORKER_TIMEOUT' in reasons else 'DATA_BLOCKED' if s in blocked else
                            'FAILED' if reasons else 'COMPLETED')
                    pair=build_row(inp,s);pairs.append(pair)
                    states[s]=states[s].model_copy(update={'execution':execution,'reason_codes':reasons,'served_at':now()})
                    # Output is a distinct resumable stage; packet construction never invokes a detector.
                    packet_dep=digest([pair[0].row_id,[b.result_id for b in pair[1]],extra,
                        hashlib.sha256((code/'src/pcs/selection/packets.py').read_bytes()).hexdigest()])
                    execute('PACKET',lambda _,i=inp,symbol=s,e=extra:build_decision_evidence_packet(
                        DecisionPacketInput(symbol=symbol,selection_input=i,extra_records=e)),spec.budgets.output_seconds,[s],{s:packet_dep})
                    if 'PACKET' in values[s]:
                        packet=DecisionEvidencePacket.model_validate(values[s]['PACKET'])
                        refs=sorted({ref for k in pair[0].sort_keys for ref in k.source_refs}|
                            {c['result_id'] for c in packet.component_refs})
                        if refs and resolve_evidence(EvidenceQuery(packet=packet,evidence_ids=refs)).status!='RESOLVED':
                            states[s]=states[s].model_copy(update={'execution':'FAILED','reason_codes':reasons+['RANKING_REFERENCE_UNRESOLVED']})
                        query_counts[s]=len(refs);packet_refs[s]=packet.packet_id
                    else:
                        states[s]=states[s].model_copy(update={'execution':'TIMED_OUT','reason_codes':errors[s]+['PACKET_NOT_COMMITTED']})
                    states[s]=states[s].model_copy(update={'served_attempt':store.token})
                    store.save_state(states[s]);states_summary[s]=states[s].execution
                    lineage_by_symbol[s]=states[s].lineage[-1] if states[s].lineage else {}
                    progress['completed']+=1
                    key={'COMPLETED':'execution_completed_count','DATA_BLOCKED':'data_blocked_count',
                        'FAILED':'failed_count','TIMED_OUT':'timed_out_count','UNPROCESSED':'unprocessed_count'}[states[s].execution]
                    progress['unprocessed_count']-=1
                    progress[key]+=1
                if adapter and hasattr(adapter,'release'):adapter.release(symbols)
            progress['stage']='FINALIZE';emit()
            base=RankingInput(context=spec.context,requested_symbols=spec.symbols,enabled_families=profile.families,
                opportunities=[],policy=profile.ranking,previous=previous_shortlist)
            shortlist=rank_prepared_rows(base,pairs)
            if previous_shortlist:
                changes=[]
                old_rows={r.symbol:r for r in previous_shortlist.rows}
                new_rows={r.symbol:r for r in shortlist.rows}
                for change in shortlist.changes:
                    s=change['symbol'];proof=lineage_by_symbol.get(s,{})
                    reasons=change['reason_codes']
                    if previous_profile!=profile.model_dump(mode='json'):
                        reasons=[r for r in reasons if r!='UNCHANGED']+['POLICY_CHANGED']
                    if s in old_rows and s in new_rows:
                        before={a.family:a for a in old_rows[s].family_assessments}
                        for assessment in new_rows[s].family_assessments:
                            old=before[assessment.family]
                            if assessment.execution=='EXECUTED' and old.execution=='NOT_EXECUTED':
                                reasons.append('EVIDENCE_COMPLETED')
                            if ((assessment.entry_window_elapsed_at_requested_session is True and old.entry_window_elapsed_at_requested_session is not True) or
                                (assessment.confirmation_deadline_elapsed_at_requested_session is True and old.confirmation_deadline_elapsed_at_requested_session is not True)):
                                reasons.append('WINDOW_OR_CONFIRMATION_DEADLINE_ELAPSED')
                    if proof.get('reason')=='HISTORICAL_DATA_REVISED':
                        reasons=[r for r in reasons if r!='NEW_CONFIRMATION']+['HISTORICAL_DATA_REVISED','EVIDENCE_REPLAYED']
                    changes.append(dict(**{k:v for k,v in change.items() if k!='reason_codes'},reason_codes=sorted(set(reasons)),
                        previous_result_id=old_rows[s].representative_result_id if s in old_rows else None,
                        result_id=new_rows[s].representative_result_id if s in new_rows else None,
                        requested_session=spec.context.requested_session,lineage=proof))
                shortlist=shortlist.model_copy(update={'changes':changes})
                from pcs.selection.storage import shortlist_identity
                shortlist=shortlist.model_copy(update={'shortlist_id':shortlist_identity(shortlist)})
            shortlist=shortlist.model_copy(update={'rows':[r.model_copy(update={'packet_id':packet_refs.get(r.symbol)}) for r in shortlist.rows]})
            count=Counter(states_summary.values())
            summary={k:count[state] for k,state in [('execution_completed_count','COMPLETED'),('data_blocked_count','DATA_BLOCKED'),
                ('failed_count','FAILED'),('timed_out_count','TIMED_OUT'),('unprocessed_count','UNPROCESSED')]}
            assert sum(summary.values())==len(spec.symbols)
            summary.update(requested_count=len(spec.symbols),elapsed_seconds=perf_counter()-started)
            groups={g:sum(r.group==g for r in shortlist.rows) for g in GROUPS}
            for f in profile.families:
                assessments=[a for r in shortlist.rows for a in r.family_assessments if a.family==f]
                family_counts[f]=dict(execution=dict(Counter(a.execution for a in assessments)),
                    states=dict(Counter(a.state or 'UNKNOWN' for a in assessments)),current_true=sum(a.eligible_at_requested_time is True for a in assessments),
                    applicability=dict(Counter(a.applicability for a in assessments)),
                    deadline_elapsed=sum(a.confirmation_deadline_elapsed_at_requested_session is True for a in assessments),
                    window_elapsed=sum(a.entry_window_elapsed_at_requested_session is True for a in assessments),
                    current_unknown=sum(a.eligible_at_requested_time is None for a in assessments))
            coverage='COMPLETE' if shortlist.coverage_complete and count['COMPLETED']==len(spec.symbols) else 'PARTIAL'
            execution='COMPLETED' if not count['FAILED'] and not count['TIMED_OUT'] and not count['UNPROCESSED'] else 'PARTIAL'
            audit=saved.read_audit if saved else {'counts':adapter.counts,'source_verification':adapter.verify_unchanged(),
                'reads':getattr(getattr(adapter,'reader',None),'audit',[])}
            if any(hashlib.sha256(Path(path).read_bytes()).hexdigest()!=checksum for path,checksum in config_snapshot.items()):
                raise ValueError('OBSERVATION_CONFIG_CHANGED_DURING_RUN')
            run=StockObservationRun(run_id=spec.run_id,attempt_id=store.token,status=execution,coverage=coverage,
                context=spec.context,source_commit=source_commit,spec_id=spec_id,profile_id=digest(profile),universe_id=spec.universe_id,
                requested_symbols=spec.symbols,output_directory=str(root),checkpoint=str(store.path),summary=summary,
                selection_v2=dict(groups=groups,unique_stocks=len(spec.symbols),family_coverage=family_counts,
                    current_unknown=sum(r.current_eligible is None for r in shortlist.rows),query_reference_counts=query_counts,
                    cache_hits=progress['cache_hits'],component_counts={k:dict(v) for k,v in stage_counts.items()}),
                current_published=execution=='COMPLETED' and coverage=='COMPLETE',reason_codes=global_errors)
            documents={'observation_run.json':run.model_dump(mode='json'),'symbol_index.json':store.checkpoint['symbols'],
                'stock_shortlist.json':shortlist.model_dump(mode='json'),'stock_shortlist.ai.json':shortlist.model_dump(mode='json'),
                'shortlist_changes.json':shortlist.changes,'read_audit.json':audit,'selection_profile.schema.json':type(profile).model_json_schema(),
                'observation_spec.schema.json':StockObservationInput.model_json_schema(),
                'stage_counts.json':{k:dict(v) for k,v in stage_counts.items()}}
            hashes={name:write_json(root/name,value) for name,value in documents.items()}
            for name in ('observation_spec.json','universe.json','dependency_manifest.json'):
                hashes[name]=hashlib.sha256((root/name).read_bytes()).hexdigest()
            hashes['stock_shortlist.csv']=_write_observation_text(root/'stock_shortlist.csv',shortlist_csv(shortlist))
            hashes['stock_shortlist.zh-CN.md']=_write_observation_text(root/'stock_shortlist.zh-CN.md',shortlist_markdown(shortlist))
            write_json(root/'observation_manifest.json',dict(scope=spec.scope,version='1.0',run_id=spec.run_id,
                attempt_id=store.token,status=run.status,coverage=run.coverage,source_commit=source_commit,sha256=hashes))
            store.checkpoint['status']=run.status;store.flush()
            write_json(Path(spec.output_directory)/'LATEST_ATTEMPT.json',dict(run_id=spec.run_id,directory=str(root),status=run.status,coverage=coverage))
            if run.current_published:
                write_json(Path(spec.output_directory)/'CURRENT.json',dict(scope=spec.scope,run_id=spec.run_id,directory=str(root),as_of=spec.context.requested_as_of))
            return run
        finally:
            stop.set();thread.join(timeout=1)


def _write_observation_text(path,value):
    from .artifacts import _write_atomic
    return _write_atomic(path,value)


def _evidence_record(value):
    """Serialize real result objects and lightweight test doubles safely."""
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    values = getattr(value, "__dict__", None)
    return dict(values) if values is not None else None


def _trend_rule_context(config: TrendIndicatorConfig) -> dict[str, Any]:
    """Keep the persisted rule context compact and limited to used gates."""
    keys = (
        "pivot_left_bars", "pivot_right_bars", "minimum_swing_price_change_pct",
        "pullback_no_pullback_max_pct", "pullback_shallow_max_pct",
        "pullback_healthy_min_pct", "pullback_healthy_max_pct",
        "pullback_sma20_near_atr", "pullback_sma50_near_atr",
        "pullback_extended_above_sma20_atr", "pullback_extended_above_sma50_atr",
        "pullback_breakdown_below_sma50_atr", "support_nearby_atr",
        "support_nearby_pct", "support_cluster_tolerance_atr", "rsi_overheated",
        "rsi_hard_block", "upper_wick_rejection_atr",
        "upper_rejection_close_location", "minimum_confirmation_rvol",
    )
    return {key: getattr(config, key) for key in keys}


@dataclass(frozen=True)
class DailyReadiness:
    status: str
    reason_codes: tuple[str, ...] = ()


def _validate_options_quote_session(chain: pd.DataFrame, expected_session, *, mode="EOD",
                                    decision_time=None, exchange_timezone="America/New_York",
                                    max_quote_age_seconds=300.0) -> pd.DataFrame:
    """Validate quote timing without replacing source timestamps with dates."""
    expected = pd.Timestamp(expected_session).date()
    mode = str(mode).upper()
    if chain is None or chain.empty:
        raise ValueError("OPTIONS_QUOTE_SESSION_UNVERIFIED")
    sessions = set()
    intraday_timestamp_incomplete = False
    if "trade_date" in chain.columns:
        sessions.update(pd.Timestamp(value).date() for value in chain["trade_date"]
                        if not pd.isna(value) and not pd.isna(pd.to_datetime(value, errors="coerce")))
    quote_times = []
    for field in ("quote_as_of", "as_of"):
        if field not in chain.columns:
            continue
        for raw in chain[field]:
            if pd.isna(raw):
                continue
            stamp = pd.Timestamp(raw)
            if mode == "INTRADAY" and stamp.tzinfo is None:
                intraday_timestamp_incomplete = True
            if stamp.tzinfo is not None:
                stamp = stamp.tz_convert(exchange_timezone)
            sessions.add(stamp.date())
            if field == "quote_as_of" and stamp.tzinfo is not None:
                quote_times.append(stamp.tz_convert("UTC"))
    if not sessions:
        raise ValueError("OPTIONS_QUOTE_SESSION_UNVERIFIED")
    if len(sessions) != 1 or expected not in sessions:
        raise ValueError("OPTIONS_QUOTE_SESSION_MISMATCH")
    if mode == "INTRADAY":
        if intraday_timestamp_incomplete or len(quote_times) != len(chain):
            raise ValueError("OPTION_QUOTE_TIMESTAMP_REQUIRED")
        decision = pd.Timestamp(decision_time)
        if pd.isna(decision) or decision.tzinfo is None:
            raise ValueError("OPTIONS_DECISION_TIMEZONE_REQUIRED")
        decision = decision.tz_convert("UTC")
        ages = [(decision - stamp).total_seconds() for stamp in quote_times]
        if any(age < 0 for age in ages):
            raise ValueError("OPTION_QUOTE_FROM_FUTURE")
        if any(age > float(max_quote_age_seconds) for age in ages):
            raise ValueError("OPTION_QUOTE_STALE")
    # Return a copy, but preserve the full quote time, source timezone and raw
    # timestamp/unit columns.  Session derivation must never mutate evidence.
    return chain.copy()


def _execution_identity(option_rules):
    """Fingerprint the loaded source tree and effective option rules for audit."""
    import hashlib
    import json
    source_root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(source_root.rglob("*.py")):
        digest.update(path.relative_to(source_root).as_posix().encode())
        digest.update(path.read_bytes())
    code = "sha256:" + digest.hexdigest()
    digest.update(json.dumps(option_rules, sort_keys=True, default=str).encode())
    return code, "sha256:" + digest.hexdigest()


def _candidate_record(candidate) -> dict:
    """Serialize the canonical candidate without retaining a live object."""
    if hasattr(candidate, "to_dict"):
        return dict(candidate.to_dict())
    try:
        return dict(asdict(candidate))
    except TypeError:
        return dict(vars(candidate))


def _daily_preflight(symbols, access, decision_date, manifest_snapshot=None):
    """Build one stable, read-only daily readiness index for this run."""
    if not hasattr(access, "_resolve_route") or not hasattr(access, "_read_manifest"):
        return {str(symbol).strip().upper(): DailyReadiness("READY") for symbol in symbols}
    manifest_cache = {}
    index = {}
    decision = pd.Timestamp(decision_date).normalize()
    for symbol in symbols:
        s = str(symbol).strip().upper()
        try:
            try:
                _, manifest_path, _ = access._resolve_route("daily", s)
            except Exception:
                from pcs.data.control_plane import SourceResolver
                if SourceResolver().resolve("daily"):
                    index[s] = DailyReadiness("PREP_REQUIRED", ("MANIFEST_ROUTE_MISSING",))
                else:
                    index[s] = DailyReadiness("HARD_BLOCKED", ("BLOCKED_NO_AUTHORIZED_SOURCE",))
                continue
            key = str(Path(manifest_path).resolve())
            if manifest_snapshot is not None and key == str(Path(manifest_snapshot.path).resolve()):
                manifest = manifest_snapshot.rows_for("daily", s)
                if manifest.empty and manifest_snapshot.rows:
                    index[s] = DailyReadiness("PREP_REQUIRED", ("ACTIVE_GENERATION_MISSING",))
                    continue
            elif key not in manifest_cache:
                manifest_cache[key] = access._read_manifest(Path(manifest_path))
                manifest = manifest_cache[key]
            else:
                manifest = manifest_cache[key]
            required = {"dataset", "symbol", "active_generation", "min_date", "max_date"}
            if manifest.empty or not required.issubset(manifest.columns):
                index[s] = DailyReadiness("PREP_REQUIRED", ("MANIFEST_ROUTE_MISSING",)); continue
            rows = manifest[(manifest.dataset.astype(str) == "daily") &
                            manifest.symbol.astype(str).str.upper().eq(s)]
            active = rows[rows.active_generation.notna() &
                          rows.active_generation.astype(str).str.strip().ne("") &
                          rows.active_generation.astype(str).str.lower().ne("nan")]
            if active.empty:
                index[s] = DailyReadiness("PREP_REQUIRED", ("ACTIVE_GENERATION_MISSING",)); continue
            covered = active[pd.to_datetime(active.min_date, errors="coerce").le(decision) &
                            pd.to_datetime(active.max_date, errors="coerce").ge(decision)]
            if covered.empty:
                max_date = pd.to_datetime(active.max_date, errors="coerce").max()
                reason = "DAILY_STALE" if pd.notna(max_date) and max_date < decision else "INSUFFICIENT_FEATURE_WARMUP"
                index[s] = DailyReadiness("PREP_REQUIRED", (reason,)); continue
            # Latest-session coverage and historical warmup are independent.
            # A current quarter is normally much smaller than the 200-row
            # indicator warmup, so count all active, non-future partitions.
            # This is only an upper bound. The independent verified audit
            # below counts PIT rows; a partition spanning the requested session
            # must not be discarded merely because it also contains later rows.
            history = active[pd.to_datetime(active.min_date, errors="coerce").le(decision)].copy()
            identity_columns = [column for column in ("partition_ids", "parquet_path", "active_generation")
                                if column in history.columns]
            if identity_columns:
                history = history.drop_duplicates(identity_columns, keep="last")
            if "row_count" not in history:
                index[s] = DailyReadiness("PREP_REQUIRED", ("INSUFFICIENT_FEATURE_WARMUP",)); continue
            counts = pd.to_numeric(history.row_count, errors="coerce")
            if counts.isna().any() or counts.sum() < 200:
                index[s] = DailyReadiness("PREP_REQUIRED", ("INSUFFICIENT_FEATURE_WARMUP",)); continue
            index[s] = DailyReadiness("READY")
        except Exception as exc:
            index[s] = DailyReadiness("HARD_BLOCKED", (str(exc).strip() or "DAILY_READINESS_UNAVAILABLE",))
    return index


def _daily_requirements(symbol: str, effective_daily_session: str) -> MarketDataRequirements:
    day = pd.Timestamp(effective_daily_session).normalize()
    return MarketDataRequirements(
        symbol=symbol,
        required_start=str((day - pd.Timedelta(days=420)).date()),
        required_end=str(day.date()),
        datasets=("daily",),
        decision_as_of=str(day.date()),
        required_history_rows=200,
    )


def _prepare_daily_symbol(symbol: str, access: PCSDataAccess, effective_daily_session: str) -> dict:
    """Prepare one daily dependency through the canonical control plane."""
    symbol = str(symbol).strip().upper()
    req = _daily_requirements(symbol, effective_daily_session)
    try:
        # An independently verified active generation is authoritative.  Do
        # not let stale legacy migration files prevent its incremental refresh.
        active_state = _daily_preflight((symbol,), access, effective_daily_session)[symbol]
        if active_state.status == "READY":
            return {"symbol": symbol, "attempted": False, "result": None,
                    "result_status": "ALREADY_COMPLETE", "reason_codes": (),
                    "provider_calls": 0, "provider_coverage_count": 0, "promotion_calls": 0,
                    "admission_status": "ACTIVE_CANONICAL_READY"}
        if active_state.status == "PREP_REQUIRED" and any(code in {"DAILY_STALE", "CANONICAL_DAILY_STALE"} for code in active_state.reason_codes):
            result = ensure_market_data(symbol, req, access=access)
            outcomes = tuple(getattr(result, "import_outcomes", ()) or ())
            return {"symbol": symbol, "attempted": True, "result": result,
                    "result_status": str(getattr(result, "status", "")),
                    "reason_codes": tuple(getattr(result, "reason_codes", ()) or ()) or active_state.reason_codes,
                    "provider_calls": sum(1 for item in outcomes if str(item.get("status", "")).upper() != "REUSED"),
                    "provider_coverage_count": len(getattr(result, "provider_coverage", ()) or ()),
                    "promotion_calls": len(getattr(result, "promoted_partitions", ()) or ()),
                    "promoted_partitions": tuple(getattr(result, "promoted_partitions", ()) or ()),
                    "promotion_receipts": tuple(getattr(result, "promoted_partitions", ()) or ()),
                    "admission_status": "ACTIVE_CANONICAL_STALE"}
        admission = admit_migrated_daily_symbol(symbol, decision_as_of=effective_daily_session,
                                                required_start=req.required_start,
                                                required_warmup_sessions=200, data_access=access)
        admission_status = str(admission.get("status", ""))
        admission_reasons = tuple(admission.get("reason_codes", ()) or ())
        if admission_status == "MIGRATED_CANONICAL_INVALID" or admission_status in {"MIGRATION_CATALOG_AMBIGUOUS", "MIGRATION_CATALOG_NOT_SUCCESS"}:
            return {"symbol": str(symbol).upper(), "attempted": True, "result": None,
                    "result_status": admission_status, "reason_codes": admission_reasons,
                    "provider_calls": 0, "provider_coverage_count": 0, "promotion_calls": 0,
                    "admission_status": admission_status, "admission_result": admission}
        needs_incremental = admission_status == "ADMITTED_NEEDS_INCREMENTAL"
        result = ensure_market_data(symbol, req, access=access) if (
            admission_status in {"MIGRATION_CATALOG_MISSING", "MIGRATION_CATALOG_NOT_SUCCESS",
                                 "MIGRATION_PHYSICAL_MISSING", ""} or needs_incremental) else None
        status = str(getattr(result, "status", ""))
        reasons = tuple(dict.fromkeys((*admission_reasons, *(getattr(result, "reason_codes", ()) or ()))))
        return {"symbol": str(symbol).upper(), "attempted": True, "result": result,
                "result_status": status or admission_status, "reason_codes": reasons,
                "provider_calls": sum(1 for item in (getattr(result, "import_outcomes", ()) or ())
                                      if str(item.get("status", "")).upper() != "REUSED"),
                "provider_coverage_count": len(getattr(result, "provider_coverage", ()) or ()),
                "promotion_calls": len(getattr(result, "promoted_partitions", ()) or ()) + len(admission.get("promoted_partitions", ()) or ()),
                "promoted_partitions": tuple(getattr(result, "promoted_partitions", ()) or ()) + tuple(admission.get("promoted_partitions", ()) or ()),
                "promotion_receipts": tuple(getattr(result, "promoted_partitions", ()) or ()) + tuple(admission.get("promotion_receipts", ()) or ()),
                "admission_status": admission_status, "admission_result": admission}
    except Exception as exc:
        return {"symbol": str(symbol).upper(), "attempted": True, "result": None,
                "result_status": "FAILED", "reason_codes": (str(exc).strip() or type(exc).__name__,),
                "provider_calls": 0, "provider_coverage_count": 0, "promotion_calls": 0}


def _bounded_daily_preparation(symbols, access, effective_daily_session, *, max_workers, timeout_seconds):
    # Windows cannot safely replace the shared CSV manifest while several
    # preparation threads are committing generations.  Keep the canonical
    # promotion path single-writer on that platform; provider/read-only scans
    # remain bounded by the caller's worker setting.
    preparation_workers = 1 if os.name == "nt" else max_workers
    outcomes = run_symbol_workers(
        symbols, lambda symbol: _prepare_daily_symbol(symbol, access, effective_daily_session),
        max_workers=preparation_workers, timeout_seconds=timeout_seconds, include_error_details=True,
    )
    results = {}
    for outcome in outcomes:
        if outcome.value is not None:
            results[outcome.symbol] = outcome.value
            continue
        not_started = "STAGE_DEADLINE_NOT_STARTED" in outcome.reason_codes
        timed_out = not_started or "WORKER_TIMEOUT" in outcome.reason_codes
        results[outcome.symbol] = {
            "symbol": outcome.symbol, "attempted": not not_started,
            "result_status": "TIMEOUT" if timed_out else "FAILED",
            "reason_codes": (("DAILY_PREPARATION_NOT_STARTED",) if not_started else
                             ("DAILY_PREPARATION_TIMEOUT",) if timed_out else outcome.reason_codes),
            "provider_calls": 0, "provider_coverage_count": 0, "promotion_calls": 0,
        }
    counters = {"attempted": sum(bool(x.get("attempted")) for x in results.values()),
                "provider_calls": sum(x.get("provider_calls", 0) for x in results.values()),
                "provider_coverage_count": sum(x.get("provider_coverage_count", 0) for x in results.values()),
                "promotion_calls": sum(x.get("promotion_calls", 0) for x in results.values())}
    return results, counters


def _revalidate_daily(symbols, access, effective_daily_session, resolver):
    """Re-read readiness and pin-test each prepared daily dependency."""
    states = _daily_preflight(symbols, access, effective_daily_session)
    for symbol in symbols:
        state = states[symbol]
        if state.status != "READY":
            continue
        try:
            resolver(symbol, effective_daily_session, 200, data_access=access)
        except Exception as exc:
            states[symbol] = DailyReadiness(
                "PREP_REQUIRED", (str(exc).strip() or "DAILY_VERIFIED_READ_FAILED",))
    return states


def _audit_verified_daily(states, symbols, access, effective_daily_session, resolver,
                          *, max_workers=8, timeout_seconds=60.0,
                          manifest_snapshot=None, runtime=None):
    """Verify metadata-READY dependencies without fetching or writing."""
    if not hasattr(access, "_resolve_route") or not hasattr(access, "_read_manifest"):
        return states
    hard_codes = {
        "DATASET_CHECKSUM_MISMATCH", "READ_BACK_CHECKSUM_MISMATCH",
        "DATASET_PROVENANCE_INCOMPLETE", "DUPLICATE_CANONICAL_PRICE_KEY",
        "GENERATION_NOT_VERIFIED", "CANONICAL_PERMISSION_REPAIR_REQUIRES_OWNER",
    }
    candidates = tuple(symbol for symbol in symbols if states[symbol].status == "READY")
    def audit(symbol):
        try:
            if runtime is not None:
                runtime.resolve_daily_handle(symbol, effective_daily_session, 200,
                                             resolver=resolver)
            else:
                resolver(symbol, effective_daily_session, 200, data_access=access)
            return None
        except Exception as exc:
            return str(exc).strip() or "DAILY_VERIFIED_READ_FAILED"
    from .concurrency import run_symbol_workers
    outcomes = run_symbol_workers(candidates, audit, max_workers=max_workers,
                                  timeout_seconds=timeout_seconds, include_error_details=True)
    for outcome in outcomes:
        if outcome.value is not None:
            code = outcome.value
            states[outcome.symbol] = DailyReadiness(
                "HARD_BLOCKED" if code in hard_codes else "PREP_REQUIRED", (code,))
        elif outcome.reason_codes:
            states[outcome.symbol] = DailyReadiness("PREP_REQUIRED",
                ("DAILY_VERIFIED_READ_TIMEOUT" if "WORKER_TIMEOUT" in outcome.reason_codes or
                 "STAGE_DEADLINE_NOT_STARTED" in outcome.reason_codes else outcome.reason_codes[-1],))
    return states


def _evaluate_symbol(symbol, *, run_id, asof, access, benchmark, benchmark_symbol,
                     options_reader, option_rules, daily_asof=None, static_metadata_reader=None,
                     daily_handle_resolver=None, auto_prepare_data=False,
                     refresh_policy="INCREMENTAL_IF_NEEDED", runtime=None,
                     options_prepare=None, options_enabled=None, mode="EOD",
                     contract_selector=None, market_state_reader=None,
                     portfolio_context_reader=None, event_calendar_reader=None, resume_row=None,
                     evidence_window=60, saved_stage=None, on_stage=None):
    started = perf_counter()
    stage_timings = {}
    selected_contract = None
    selection_result = None
    selection_reasons = ()
    selection_identity = None
    trend = None
    trend_config = TrendIndicatorConfig()
    metadata = static_metadata_reader(symbol) if static_metadata_reader is not None else None
    entry = evaluate_static_eligibility(symbol, metadata)
    if entry.status != EligibilityStatus.PCS_ELIGIBLE:
        return TickerScanResult(symbol, run_id, asof, entry.status,
            final_action=FinalAction.DATA_FAILED, reason_codes=entry.reason_codes,
            latency_ms=(perf_counter()-started)*1000)
    try:
        resolver = daily_handle_resolver or resolve_active_verified_daily_handle
        day = pd.Timestamp(daily_asof or asof).normalize()

        # The scanner never prepares data; only the run orchestrator may write.
        runtime = runtime or PoolRuntime(access=access)
        io_started = perf_counter()
        handle = runtime.resolve_daily_handle(
            symbol, daily_asof or asof, 200, resolver=resolver)
        daily = runtime.read_daily(handle, end_date=daily_asof or asof,
                                   required_warmup_rows=200)
        stage_timings["daily_read_and_verify"] = (perf_counter() - io_started) * 1000
        if benchmark is None or daily.empty:
            raise ValueError("BENCHMARK_OR_DAILY_DATA_UNAVAILABLE")
        # Each worker receives an independent immutable snapshot boundary;
        # trend helpers may construct intermediate columns internally.
        if resume_row is not None:
            timing = resume_row.timing_status
            action = FinalAction.WAIT
            timing_reasons = tuple(resume_row.candidate_state["timing_reason_codes"])
            timing_warnings = list(resume_row.warnings)
            feature_date = resume_row.feature_max_date
            close = resume_row.candidate_state["close"]
            atr = resume_row.candidate_state["atr"]
            engine = trend_gate = pullback_gate = None
        elif saved_stage:
            state = saved_stage.stage_state
            timing, action = TimingStatus(state["timing"]), FinalAction(state["action"])
            timing_reasons = tuple(state["reasons"])
            timing_warnings = list(state["warnings"])
            trend_gate = SimpleNamespace(reasons=state["trend_reasons"])
            pullback_gate = SimpleNamespace(reasons=state["pullback_reasons"])
            trend = SimpleNamespace(market_structure_engine=SimpleNamespace(**state["engine"]),
                                    support=SimpleNamespace(current_atr=state["atr"]))
            stage_timings["trend_and_timing"] = 0.0
            engine = trend.market_structure_engine
            feature_date = engine.feature_max_date
            close = float(daily.iloc[-1].close)
            atr = state["atr"]
        else:
            # Each worker receives an independent immutable snapshot boundary;
            # trend helpers may construct intermediate columns internally.
            timing_reasons = []
            timing_warnings = []
            trend_gate = pullback_gate = interpretation = trend_score = trend = None
            timing_started = perf_counter()
            try:
                trend_config = TrendIndicatorConfig()
                trend = runtime.observe(symbol, "indicators", lambda: build_trend_snapshot(
                    daily.copy(deep=True), benchmark.copy(deep=True),
                    as_of_date=str(day.date()), symbol=symbol, benchmark=benchmark_symbol,
                    config=trend_config, evidence_window=evidence_window))
                interpretation = runtime.observe(symbol, "trend_interpretation", lambda: interpret_trend(trend))
                trend_score = runtime.observe(symbol, "trend_score", lambda: score_trend(trend, interpretation))
                trend_gate = runtime.observe(symbol, "trend_gate", lambda: evaluate_trend_gate(trend_score, interpretation, trend))
                pullback_gate = runtime.observe(symbol, "timing_gate", lambda: evaluate_pullback_gate(trend_gate, trend, interpretation))
                timing_warnings = list(getattr(trend, "warnings", ()) or ())
                for result in (interpretation, trend_score, trend_gate, pullback_gate):
                    timing_warnings.extend(getattr(result, "warnings", ()) or ())
                timing_warnings = list(dict.fromkeys(timing_warnings))
                timing_reasons.extend(getattr(interpretation, "reasons", ()) or ())
                timing_reasons.extend(getattr(trend_score, "reasons", ()) or ())
                timing_reasons.extend(getattr(trend_gate, "reasons", ()) or ())
                timing_reasons.extend(getattr(pullback_gate, "reasons", ()) or ())
                if not all(getattr(result, "available", False) for result in
                           (trend, interpretation, trend_score, trend_gate, pullback_gate)):
                    timing, action = TimingStatus.WAIT, FinalAction.WAIT
                    timing_reasons.insert(0, "TIMING_EVIDENCE_UNAVAILABLE")
                elif trend_gate.trend_gate_result == "REJECT" or pullback_gate.pullback_gate_result == "REJECT":
                    timing, action = TimingStatus.WAIT, FinalAction.REJECTED
                    timing_reasons.insert(0, "UNDERLYING_STRUCTURAL_REJECT")
                elif trend_gate.trend_gate_result == "WATCH":
                    timing, action = TimingStatus.WATCH, FinalAction.WATCH
                elif (trend_gate.trend_gate_result == "PASS" and
                      pullback_gate.pullback_gate_result == "WAIT"):
                    timing, action = TimingStatus.WAIT, FinalAction.WAIT
                elif (trend_gate.trend_gate_result == "PASS" and
                      pullback_gate.pullback_gate_result == "PASS"):
                    timing, action = TimingStatus.TIMING_ENTRY_READY, FinalAction.WAIT
                else:
                    timing, action = TimingStatus.WAIT, FinalAction.WAIT
            except Exception:
                timing, action = TimingStatus.WAIT, FinalAction.WAIT
                timing_reasons = ["TIMING_EVIDENCE_UNAVAILABLE"]
            timing_reasons.extend(timing_warnings)
            timing_reasons = tuple(dict.fromkeys(timing_reasons))
            stage_timings["trend_and_timing"] = (perf_counter() - timing_started) * 1000
            options_status, option_reasons = OptionsStatus.NOT_EVALUATED, ()
            candidates = ()
            discovered_contracts = ()
            engine = getattr(trend, "market_structure_engine", None)
            feature_date = getattr(engine, "feature_max_date", None)
            close = float(daily.iloc[-1].close)
            atr = float(getattr(getattr(trend, "support", None), "current_atr", 0) or 0)
        options_status, option_reasons = OptionsStatus.NOT_EVALUATED, ()
        candidates, discovered_contracts = (), ()
        option_identity = None
        timing_evidence = (dict(saved_stage.stage_state.get("evidence", {})) if saved_stage else {
            "price_indicator_series": list(getattr(trend, "evidence_series", ()) or ()),
            "trend_evidence": {
                "market_structure": _evidence_record(getattr(trend, "market_structure", None)),
                "support": _evidence_record(getattr(trend, "support", None)),
                "relative_strength": _evidence_record(getattr(trend, "relative_strength", None)),
                "market_structure_engine": _evidence_record(engine),
            },
            "applicable_rules": _trend_rule_context(trend_config),
        })
        timing_state = {
                    "evidence": timing_evidence,
                    "timing": timing.value, "action": action.value,
                    "reasons": timing_reasons, "warnings": timing_warnings,
                    "trend_reasons": tuple(getattr(trend_gate, "reasons", ()) or ()),
                    "pullback_reasons": tuple(getattr(pullback_gate, "reasons", ()) or ()),
                    "engine": {key: (feature_date if key == "feature_max_date" else getattr(engine, key, None)) for key in
                               ("feature_max_date", "structural_trend", "short_term_phase")},
                    "atr": atr}
        if on_stage is not None and saved_stage is None:
            on_stage(TickerScanResult(symbol, run_id, asof, entry.status, timing,
                final_action=action, reason_codes=timing_reasons, checkpoint_stage="OPTIONS_PENDING",
                stage_state=timing_state, stage_timings_ms=dict(stage_timings)))
        if options_enabled is None:
            options_enabled = options_reader is not None
        if (timing == TimingStatus.TIMING_ENTRY_READY and
                mode in {"PREMARKET", "INTRADAY"} and options_reader is None):
            option_reasons = ("LIVE_OPTIONS_SOURCE_REQUIRED",)
        elif timing == TimingStatus.TIMING_ENTRY_READY and options_enabled:
            options_started = perf_counter()
            try:
                option_day = (pd.Timestamp(asof).normalize() if options_reader is not None and
                              mode in {"PREMARKET", "INTRADAY"} else pd.Timestamp(feature_date).normalize())
                if options_reader is not None:
                    chain = runtime.read_options(
                        symbol=symbol, trade_date=option_day, reader=options_reader)
                else:
                    option_handle = runtime.resolve_options(symbol, str(option_day.date()))
                    chain = runtime.read_options_handle(option_handle, start_date=str(option_day.date()), end_date=str(option_day.date()))
                chain = _validate_options_quote_session(
                    chain, option_day, mode=mode, decision_time=asof)
                if options_reader is None:
                    option_identity = list(runtime._handle_key(option_handle))
                contract_entry_date = option_day
                candidates = runtime.observe(symbol, "options_discovery", lambda: discover_spreads(
                    symbol, contract_entry_date, close, atr, chain, rules=option_rules))
                selected_contract = None
                selection_result = None
                selection_reasons = ()
                selection_identity = None
                if candidates and contract_selector is None:
                    options_status = OptionsStatus.DISCOVERED
                    selection_reasons = ("CONTRACT_SELECTION_NOT_CONNECTED",)
                elif candidates and contract_selector is not None:
                    selector = contract_selector
                    if hasattr(selector, "prepare_selector"):
                        if mode != "EOD":
                            raise ValueError("POOL_CONTEXT_EOD_ONLY")
                        selector = selector.prepare_selector(symbol=symbol, day=str(option_day.date()),
                            daily=daily, handle=handle, chain=chain, runtime=runtime, access=access)
                    decisions = [selector(
                        candidate, symbol=symbol, feature_date=str(feature_date),
                        market_state=(market_state_reader(symbol, daily, benchmark)
                                      if market_state_reader is not None else None),
                        portfolio=(portfolio_context_reader(symbol, candidate)
                                   if portfolio_context_reader is not None else None),
                        event_calendar=(event_calendar_reader(symbol, candidate)
                                        if event_calendar_reader is not None else None))
                                 for candidate in candidates]
                    accepted = next((item for item in decisions
                                     if str(item.get("status", "")).upper() == "PASS"), None)
                    if accepted is None:
                        options_status = (OptionsStatus.DATA_BLOCKED if any(item.get("status") == "DATA_BLOCKED" for item in decisions)
                                          else OptionsStatus.REJECT)
                        selection_result = decisions[0]
                        selection_reasons = tuple(dict.fromkeys(
                            code for item in decisions for code in item.get("reason_codes", ())
                        )) or ("CONTRACT_SELECTION_REJECTED",)
                    else:
                        options_status = OptionsStatus.PASS
                        selected_contract = accepted.get("contract") or accepted.get("selected_contract")
                        selection_result = accepted
                        selection_reasons = tuple(accepted.get("reason_codes", ()))
                        selection_identity = accepted.get("data_identity")
                else:
                    options_status = OptionsStatus.REJECT
                    selection_reasons = ("NO_STRUCTURALLY_VALID_PCS",)
                option_reasons = tuple(dict.fromkeys(
                    (("CONTRACT_CANDIDATES_DISCOVERED",) if candidates else ()) + selection_reasons))
                discovered_contracts = tuple(_candidate_record(candidate) for candidate in candidates)
            except Exception as exc:
                options_status = OptionsStatus.DATA_BLOCKED
                option_reasons = (_safe_reason(exc),)
            stage_timings["options"] = (perf_counter() - options_started) * 1000
        reasons = timing_reasons + option_reasons or ("TIMING_EVALUATED",)
        evaluated = TickerScanResult(symbol, run_id, asof, entry.status, timing, options_status,
            final_action=action, reason_codes=reasons, feature_max_date=str(feature_date),
            latency_ms=(perf_counter()-started)*1000,
            stage_timings_ms=stage_timings,
            cache_hits=("CHECKPOINT:TIMING",) if saved_stage else (),
            stage_state=timing_state,
            spread_count=len(candidates),
            discovered_contracts=discovered_contracts,
            selected_contract=selected_contract,
            selection_result=selection_result,
            selection_reason_codes=selection_reasons,
            selection_data_identity=selection_identity,
            structural_trend=getattr(engine, "structural_trend", None),
            short_term_phase=getattr(engine, "short_term_phase", None),
            trend_gate_reasons=tuple(getattr(trend_gate, "reasons", ()) or ()),
            pullback_gate_reasons=tuple(getattr(pullback_gate, "reasons", ()) or ()),
            warnings=tuple(timing_warnings),
            generation_id=getattr(handle, "generation_id", None),
            dataset_fingerprint=getattr(handle, "dataset_fingerprint", None),
            candidate_state={
                "decision_as_of": asof, "market_data_as_of": str(day.date()),
                "decision_mode": "EOD_SCAN" if contract_selector is None and mode == "EOD" else mode,
                "timing_computed_at": (resume_row.candidate_state["timing_computed_at"] if resume_row
                                       else datetime.now(timezone.utc).isoformat()),
                "timing_reason_codes": list(timing_reasons), "close": close, "atr": atr,
                **(timing_evidence if resume_row is None else {}),
                "daily_identity": list(runtime._handle_key(handle)),
                "options_identity": option_identity,
                "options_evaluation_reused": False,
                "options_data_status": "VERIFIED" if option_identity else "NOT_VERIFIED",
                "contract_evaluation_status": options_status.value,
                "resume_stage": ("TIMING" if timing != TimingStatus.TIMING_ENTRY_READY else
                    "OPTIONS" if options_status in {OptionsStatus.NOT_EVALUATED, OptionsStatus.DATA_BLOCKED} else "EVENT"),
            })
        if resume_row is not None:
            evaluated = replace(evaluated, structural_trend=resume_row.structural_trend,
                short_term_phase=resume_row.short_term_phase,
                trend_gate_reasons=resume_row.trend_gate_reasons,
                pullback_gate_reasons=resume_row.pullback_gate_reasons,
                candidate_state={**resume_row.candidate_state, **evaluated.candidate_state})
        return evaluated
    except Exception as exc:
        trace_dir = os.getenv("PCS_POOL_SCAN_TRACEBACK_DIR")
        if trace_dir:
            target = Path(trace_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{str(symbol).upper()}.traceback.txt").write_text(
                f"symbol={str(symbol).upper()}\nstage=timing_or_options\nexception={type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
                encoding="utf-8")
        failure_code = str(exc).strip()
        reasons = (failure_code,) if failure_code in {
            "DATASET_CHECKSUM_MISMATCH", "DATASET_FINGERPRINT_MISMATCH",
            "INSUFFICIENT_FEATURE_WARMUP", "DUPLICATE_CANONICAL_PRICE_KEY",
            "DATASET_PROVENANCE_INCOMPLETE", "GENERATION_NOT_VERIFIED",
            "ACTIVE_GENERATION_MISSING", "MANIFEST_ROUTE_MISSING",
            "DAILY_STALE", "BLOCKED_NO_AUTHORIZED_SOURCE",
            "CANONICAL_DAILY_STALE", "SOURCE_UNAVAILABLE",
        } else ("DAILY_TIMING_FAILED", type(exc).__name__)
        return TickerScanResult(symbol, run_id, asof, EligibilityStatus.DATA_BLOCKED,
            final_action=FinalAction.DATA_FAILED, reason_codes=reasons,
            latency_ms=(perf_counter()-started)*1000, stage_timings_ms=stage_timings)


def _as_of(value) -> str:
    if value == "latest":
        # Market-session semantics are keyed to the exchange's local date;
        # UTC after midnight must not advance an NYSE PREMARKET run early.
        return datetime.now(ZoneInfo("America/New_York")).isoformat()
    return pd.Timestamp(value).isoformat()


def _options_requirement(row, rules):
    # Required dates are QUOTE sessions. Expiration is a separate selector bound.
    day = str(pd.Timestamp(row.feature_max_date).date())
    return MarketDataRequirements(symbol=row.symbol, required_start=day,
        required_end=day, datasets=("options",), decision_as_of=day,
        option_type="PUT", min_dte=int(rules.get("hard_dte_min", rules.get("dte_min", 7))),
        max_dte=int(rules.get("hard_dte_max", rules.get("dte_max", 45))), required_history_rows=0)


def _safe_reason(exc):
    """Provider errors must not put request headers, bodies or secrets in reports."""
    import re
    text = str(exc).strip()
    if "reason=DATA_NOT_INGESTED_OR_CANONICAL_MANIFEST_MISSING" in text:
        return "OPTIONS_GENERATION_MISSING"
    if re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", text):
        return text
    diagnostics = getattr(exc, "diagnostics", None)
    if getattr(diagnostics, "http_status", None) in {401, 403}:
        return "CLICKHOUSE_AUTHENTICATION_FAILED"
    if getattr(diagnostics, "timeout_code", None) or isinstance(exc, TimeoutError):
        return "PROVIDER_PROBE_TIMEOUT"
    return "OPTIONS_PREPARATION_ERROR" if diagnostics else "OPTIONS_CANONICAL_VERIFICATION_FAILED"


def _options_receipt(value):
    """Keep machine evidence, never free-form provider diagnostics/configuration."""
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    allowed = {"status", "reason_codes", "selected_source", "provider_coverage", "import_outcomes",
        "promoted_partitions", "promotion_receipt", "result", "dataset", "symbol", "source_table",
        "requested_start", "requested_end", "source_min_date", "source_max_date", "physical_rows",
        "unique_contract_keys", "put_rows", "call_rows", "request_id", "generation_id",
        "promoted_generation_id", "read_back_generation_id", "checksum", "row_count", "partition",
        "path", "manifest_identity", "partition_ids", "content_hash", "final_canonical_status", "partitions"}
    def clean(item):
        if isinstance(item, Mapping):
            return {k: clean(v) for k, v in item.items() if k in allowed}
        if isinstance(item, (tuple, list)):
            return [clean(v) for v in item]
        return item if item is None or isinstance(item, (str, int, float, bool)) else str(item)
    return clean(value) if isinstance(value, Mapping) else {"status": "UNKNOWN"}


def _source_check_status(receipt):
    codes, counts = set(), []
    def visit(item):
        if isinstance(item, Mapping):
            codes.update(item.get("reason_codes", ()))
            if "physical_rows" in item:
                counts.append(int(item["physical_rows"] or 0))
            for value in item.values():
                visit(value)
        elif isinstance(item, (list, tuple)):
            for value in item:
                visit(value)
    visit(receipt)
    for code, status in (("AUTHORIZED_SOURCE_NO_ROWS", "CONFIRMED_EMPTY"),
                         ("CLICKHOUSE_AUTHENTICATION_FAILED", "AUTHENTICATION_FAILED"),
                         ("PROVIDER_PROBE_TIMEOUT", "TIMED_OUT"),
                         ("CONFIGURATION_NOT_LOADED", "CONFIGURATION_NOT_LOADED"),
                         ("CLICKHOUSE_CREDENTIALS_MISSING", "CREDENTIALS_MISSING"),
                         ("CLICKHOUSE_CONNECTION_FAILED", "CONNECTION_FAILED")):
        if code in codes:
            return status
    return "AVAILABLE" if any(counts) else "NOT_CHECKED"


def _prepare_candidate_options(row, *, runtime, requirements):
    """One standard control-plane call per need/run, with a cross-process lease.

    The existing metadata lock is nonblocking here: another preparation yields
    a recoverable result rather than a second concurrent loader or an idle wait.
    """
    import json
    key = ("prepare_options", json.dumps(asdict(requirements), sort_keys=True))
    def produce():
        lease = Path(runtime.access.manifest_path).resolve().parent / "pool_options_preparation"
        try:
            with PCSDataAccess._file_lock(lease, blocking=False):
                # Another process may have completed between scan and lease.
                runtime.refresh_options()
                try:
                    handle = runtime.resolve_options(row.symbol, requirements.required_end)
                    chain = runtime.read_options_handle(handle, start_date=requirements.required_start,
                                                        end_date=requirements.required_end)
                    _validate_options_quote_session(chain, requirements.required_end)
                    return {"status": "ALREADY_COMPLETE", "reason_codes": [], "provider_attempted": False}
                except Exception:
                    pass
                try:
                    receipt = _options_receipt(ensure_market_data(row.symbol, requirements, access=runtime.access))
                    source_status = _source_check_status(receipt)
                    return {**receipt, "control_plane_attempted": True, "source_check_status": source_status,
                            "provider_attempted": source_status in {"AVAILABLE", "CONFIRMED_EMPTY", "TIMED_OUT", "AUTHENTICATION_FAILED", "CONNECTION_FAILED"}}
                except Exception as exc:
                    return {"status": "BLOCKED", "reason_codes": [_safe_reason(exc)], "control_plane_attempted": True,
                            "provider_attempted": bool(getattr(exc, "diagnostics", None))}
        except OSError:
            return {"status": "BLOCKED", "reason_codes": ["OPTIONS_PREPARATION_IN_PROGRESS"],
                    "provider_attempted": False}
    return runtime.resolve_handle(key, produce)


def _continue_candidate(row, *, runtime, rules, allow_prepare, evaluate, save, deadline, reuse_evaluation=False):
    """Bounded prepare -> independent verified read -> options-only continuation."""
    from datetime import timedelta
    from pcs.data.clickhouse import ClickHouseConfig
    req = _options_requirement(row, rules)
    state = {"source_check_status": "NOT_CHECKED", **row.candidate_state, "requirements": asdict(req),
             "expiration_start": str((pd.Timestamp(req.required_end) + pd.Timedelta(days=req.min_dte)).date()),
             "expiration_end": str((pd.Timestamp(req.required_end) + pd.Timedelta(days=req.max_dte)).date())}
    row = replace(row, candidate_state=state)
    save(row)  # Persist timing before any I/O or mutable preparation.

    def verified():
        handle = runtime.resolve_options(row.symbol, req.required_end)
        frame = runtime.read_options_handle(handle, start_date=req.required_start, end_date=req.required_end)
        _validate_options_quote_session(frame, req.required_end)
        return list(runtime._handle_key(handle))

    previous_options_identity = state.get("options_identity")
    continuation_ready = False
    try:
        option_identity = verified()
    except Exception as exc:
        reason = _safe_reason(exc)
        row = replace(row, options_status=OptionsStatus.DATA_BLOCKED, final_action=FinalAction.WAIT,
            selected_contract=None, selection_result=None, selection_data_identity=None,
            selection_reason_codes=(), discovered_contracts=(), spread_count=0,
            reason_codes=tuple(state["timing_reason_codes"]) + tuple(dict.fromkeys(
                (*state.get("preparation_receipt", {}).get("reason_codes", ()), reason))),
            candidate_state={**state, "options_data_status": "WAITING_DATA",
                             "verified_read_status": "FAILED", "options_identity": None,
                             "options_evaluation_reused": False,
                             "contract_evaluation_status": "NOT_EVALUATED", "resume_stage": "OPTIONS"})
        now = datetime.now(timezone.utc)
        due = row.next_review_at
        if not allow_prepare:
            row = replace(row, reentry_conditions=("RUN_PREPARE_THEN_SCAN_WITH_AUTO_PREPARE_DATA",))
        elif due and now < datetime.fromisoformat(due):
            row = replace(row, reentry_conditions=("RETRY_AT_OR_AFTER_NEXT_REVIEW_AT",))
        elif perf_counter() >= deadline:
            row = replace(row, reason_codes=row.reason_codes + ("OPTIONS_PREPARATION_BUDGET_EXHAUSTED",),
                          reentry_conditions=("NEXT_RUN_WITH_PREPARATION_BUDGET",))
        else:
            budget = ClickHouseConfig.from_env()
            # The loader owns its existing finite request retries. The outer run
            # attempts a need once and never sleeps waiting for publication.
            retry_seconds = max(budget.total_timeout, budget.backoff_base * 2 ** (budget.max_attempts - 1))
            row = replace(row, next_review_at=(now + timedelta(seconds=retry_seconds)).isoformat(),
                candidate_state={**row.candidate_state, "options_data_status": "PREPARING",
                    "source_query_started_at": now.isoformat(), "attempt_budget_per_run": 1,
                    "preparation_attempt_run_id": row.run_id})
            save(row)
            receipt = _prepare_candidate_options(row, runtime=runtime, requirements=req)
            finished = datetime.now(timezone.utc)
            row = replace(row, next_review_at=(finished + timedelta(seconds=retry_seconds)).isoformat(),
                candidate_state={**row.candidate_state, "preparation_receipt": receipt,
                    "source_check_status": receipt.get("source_check_status", "NOT_CHECKED"),
                    "source_query_completed_at": finished.isoformat()})
            # SUCCESS is never sufficient. Fresh resolution and exact-session
            # readback are mandatory even when the provider claims success.
            try:
                runtime.refresh_options()
                option_identity = verified()
            except Exception as failure:
                codes = tuple(receipt.get("reason_codes", ())) + (_safe_reason(failure),)
                row = replace(row, reason_codes=tuple(state["timing_reason_codes"]) + tuple(dict.fromkeys(codes)),
                    reentry_conditions=("RETRY_AT_OR_AFTER_NEXT_REVIEW_AT",),
                    candidate_state={**row.candidate_state, "options_data_status": "WAITING_DATA",
                        "verified_read_status": "FAILED", "options_identity": None})
                save(row)
                return row
            continuation_ready = True
        if not continuation_ready:
            save(row)
            return row
    row = replace(row, next_review_at=None, reentry_conditions=(),
        candidate_state={**row.candidate_state, "options_data_status": "VERIFIED",
            "verified_read_status": "PASS", "options_identity": option_identity})
    # Only the scanner evaluates contracts; it receives the same verified runtime
    # and compact timing evidence. It cannot call the provider.
    if (reuse_evaluation and previous_options_identity == option_identity
            and row.options_status in {OptionsStatus.DISCOVERED, OptionsStatus.REJECT}):
        result = replace(row, candidate_state={**row.candidate_state, "options_evaluation_reused": True})
    else:
        result = evaluate(row)
    if result.timing_status != row.timing_status:
        result = replace(row, options_status=OptionsStatus.DATA_BLOCKED,
            reason_codes=tuple(state["timing_reason_codes"]) + ("OPTIONS_CONTINUATION_FAILED",))
    save(result)
    return result


def reconcile_pool_scan_results(before: Mapping[str, Any], after: Mapping[str, Any],
                                recovery_results: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Build an explicit ticker-level delta and optionally attach recovery evidence."""
    def rows(value):
        return {str(row.get("symbol", "")).upper(): row for row in value.get("ticker_results", ())}
    left, right = rows(before), rows(after)
    fields = ("effective_daily_session", "universe_snapshot_id", "mode", "manifest_snapshot_id",
              "code_revision", "engine_version", "profile_versions", "refresh_policy")
    identity = {f: (before.get("snapshot", {}).get(f), after.get("snapshot", {}).get(f)) for f in fields}
    def valid(value):
        if value is None or value == "" or value == {}:
            return False
        if isinstance(value, str) and value.strip().lower() in {"unknown", "none", "null"}:
            return False
        return True
    observed_fields = tuple(f for f in fields if f != "manifest_snapshot_id")
    observed_comparable = all(valid(identity[f][0]) and valid(identity[f][1]) and
                              identity[f][0] == identity[f][1] for f in observed_fields)
    comparable = observed_comparable and valid(identity["manifest_snapshot_id"][0]) and \
        valid(identity["manifest_snapshot_id"][1]) and identity["manifest_snapshot_id"][0] == identity["manifest_snapshot_id"][1]
    keys = ("eligibility_status", "initial_daily_readiness", "timing_status", "options_status", "final_action")
    changed = []
    kept = []
    for symbol in sorted(set(left) & set(right)):
        a, b = left[symbol], right[symbol]
        if any(a.get(k) != b.get(k) or a.get("reason_codes", ()) != b.get("reason_codes", ()) for k in keys):
            changed.append({"symbol": symbol, "before": {k: a.get(k) for k in (*keys, "reason_codes")},
                            "after": {k: b.get(k) for k in (*keys, "reason_codes")}})
        else:
            kept.append(symbol)
    ready_left = {s for s, row in left.items() if row.get("eligibility_status") == "PCS_ELIGIBLE"}
    ready_right = {s for s, row in right.items() if row.get("eligibility_status") == "PCS_ELIGIBLE"}
    daily_left = {s for s, row in left.items() if str(row.get("initial_daily_readiness", "")).upper() == "READY"}
    daily_right = {s for s, row in right.items() if str(row.get("initial_daily_readiness", "")).upper() == "READY"}
    recovery_by_symbol: dict[str, Any] = {}
    if recovery_results is not None:
        values = recovery_results.items() if isinstance(recovery_results, Mapping) else ((None, item) for item in recovery_results)
        for map_key, item in values:
            if isinstance(item, Mapping):
                symbol = str(item.get("symbol", map_key or "")).strip().upper()
                if symbol:
                    evidence = dict(item)
                    nested = item.get("admission_result")
                    if isinstance(nested, Mapping):
                        evidence["admission_result"] = dict(nested)
                        for key in ("status", "reason_codes", "promoted_partitions", "already_admitted",
                                    "promotion_receipts", "failed_partition", "unprocessed_partitions",
                                    "conflict_partitions", "partition_results"):
                            if key in nested:
                                evidence[f"admission_{key}"] = nested[key]
                    recovery_by_symbol[symbol] = evidence
    receipt_symbols = set(recovery_by_symbol)
    attribution = {symbol: {"supported": bool(symbol in receipt_symbols and observed_comparable),
                            "reason": "RECEIPT_AND_READ_IDENTITY" if symbol in receipt_symbols and observed_comparable
                                      else "RECOVERY_RECEIPT_OR_IDENTITY_INCOMPLETE"}
                   for symbol in sorted(set(left) | set(right)) if symbol in receipt_symbols}
    return {"comparable": comparable, "observed_comparable": observed_comparable,
            "attribution_supported": bool(attribution) and all(v["supported"] for v in attribution.values()),
            "recovery_attribution": attribution, "identity": identity, "added": sorted(set(right)-set(left)),
            "removed": sorted(set(left)-set(right)), "ready_added": sorted(ready_right-ready_left),
            "ready_removed": sorted(ready_left-ready_right),
            "daily_ready_added": sorted(daily_right-daily_left),
            "daily_ready_removed": sorted(daily_left-daily_right),
            "recovery_by_symbol": recovery_by_symbol, "changed": changed, "kept": kept,
            "before_count": len(left), "after_count": len(right)}


def summarize_recovery_results(recovery_results: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Count recovery outcomes from per-partition evidence, without plan data."""
    values = recovery_results.items() if isinstance(recovery_results, Mapping) else ((None, item) for item in recovery_results)
    counts = {"symbols": 0, "validated": 0, "reused": 0, "promoted": 0,
              "failed": 0, "unprocessed": 0, "promotion_receipts": 0}
    latest: dict[tuple[str, str], Mapping[str, Any]] = {}
    symbols: set[str] = set()
    for map_key, item in values:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol", map_key or "")).strip().upper()
        nested = item.get("admission_result") if isinstance(item.get("admission_result"), Mapping) else item
        if not symbol or not isinstance(nested, Mapping):
            continue
        symbols.add(symbol)
        partition_results = nested.get("partition_results") or ()
        for result in partition_results:
            if not isinstance(result, Mapping):
                continue
            partition = str(result.get("partition", "")).strip()
            status = str(result.get("status", "")).strip().lower()
            if not partition:
                continue
            latest[(symbol, partition)] = result
        for partition in nested.get("unprocessed_partitions") or ():
            partition = str(partition).strip()
            if partition and (symbol, partition) not in latest:
                latest[(symbol, partition)] = {"status": "unprocessed"}
        # Control-plane refreshes expose promotion receipts as structured
        # entries in promoted_partitions rather than admission partition_results.
        # Normalize them into the same ledger, but never replace richer
        # partition evidence already recorded above.
        for receipt in nested.get("promotion_receipts") or ():
            if not isinstance(receipt, Mapping):
                continue
            partitions = receipt.get("partition_ids") or receipt.get("promoted_partitions") or ()
            if isinstance(partitions, str):
                partitions = (partitions,)
            for partition in partitions:
                partition = str(partition).strip()
                if partition and (symbol, partition) not in latest:
                    latest[(symbol, partition)] = {
                        "partition": partition, "status": "PROMOTED",
                        "promotion_receipt": dict(receipt),
                    }
        for receipt in nested.get("promoted_partitions") or ():
            if not isinstance(receipt, Mapping):
                continue
            partitions = receipt.get("partition_ids") or receipt.get("promoted_partitions") or ()
            if isinstance(partitions, str):
                partitions = (partitions,)
            for partition in partitions:
                partition = str(partition).strip()
                if partition and (symbol, partition) not in latest:
                    latest[(symbol, partition)] = {
                        "partition": partition, "status": "PROMOTED",
                        "promotion_receipt": dict(receipt),
                    }
    for result in latest.values():
        status = str(result.get("status", "")).strip().lower()
        if status in counts:
            counts[status] += 1
        if status == "promoted" and result.get("promotion_receipt"):
            counts["promotion_receipts"] += 1
    counts["symbols"] = len(symbols)
    return counts


def _serialize_preparation_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Expose preparation evidence without leaking live data objects."""
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "result":
            continue
        if key == "admission_result" and isinstance(item, Mapping):
            result[key] = dict(item)
            continue
        if hasattr(item, "to_dict"):
            result[key] = item.to_dict()
        elif isinstance(item, Mapping):
            result[key] = dict(item)
        elif isinstance(item, tuple):
            result[key] = list(item)
        else:
            result[key] = item
    return result


def _checkpoint_identity(snapshot: PoolRunSnapshot, option_rules: Any, *, context=None) -> str:
    """Stable identity for a resumable scan, including code and config inputs."""
    source_root = Path(__file__).resolve().parents[1]
    code_hash = hashlib.sha256(b"".join(
        str(path.relative_to(source_root)).encode() + path.read_bytes()
        for path in sorted(source_root.rglob("*.py")))).hexdigest()
    payload = {
        "universe_snapshot_id": snapshot.universe_snapshot_id,
        "effective_daily_session": snapshot.effective_daily_session,
        "mode": snapshot.mode,
        "as_of": snapshot.as_of if snapshot.mode != "EOD" else snapshot.effective_daily_session,
        "refresh_policy": snapshot.refresh_policy,
        "code_hash": code_hash,
        "option_rules": repr(option_rules),
        "context": context,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _checkpoint_row(payload: Mapping[str, Any]) -> TickerScanResult:
    values = dict(payload)
    for field in ("reason_codes", "reentry_conditions", "trend_gate_reasons", "pullback_gate_reasons",
                  "warnings", "discovered_contracts", "cache_hits", "selection_reason_codes", "preparation_reason_codes"):
        if field in values and isinstance(values[field], list):
            values[field] = tuple(values[field])
    for field, enum in (("eligibility_status", EligibilityStatus), ("timing_status", TimingStatus),
                        ("options_status", OptionsStatus), ("final_action", FinalAction)):
        if field in values:
            values[field] = enum(values[field])
    return TickerScanResult(**values)


def _load_scan_checkpoint(path: Path, identity: str) -> tuple[str, dict[str, TickerScanResult]]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema") != "pcs.pool.scan_checkpoint" or state.get("identity") != identity:
            return "", {}
        rows = {str(symbol).upper(): _checkpoint_row(row)
                for symbol, row in (state.get("ticker_results") or {}).items()}
        return str(state.get("run_id", "")), rows
    except (OSError, ValueError, TypeError, KeyError):
        return "", {}


def _write_scan_checkpoint(path: Path, *, identity: str, run_id: str,
                           snapshot: PoolRunSnapshot, rows: Mapping[str, TickerScanResult],
                           stage: str, status: str = "IN_PROGRESS", encoded_rows=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cache = encoded_rows if encoded_rows is not None else {}
    for key in set(cache)-set(rows):
        del cache[key]
    for key, value in rows.items():
        if key not in cache or cache[key][0] is not value:
            cache[key] = (value, json.dumps(asdict(value), default=str, sort_keys=True,
                                           separators=(",", ":")))
    header = json.dumps({"schema": "pcs.pool.scan_checkpoint", "schema_version": 1,
                          "identity": identity, "run_id": run_id, "stage": stage,
                          "status": status, "updated_at": datetime.now(timezone.utc).isoformat(),
                          "snapshot": asdict(snapshot)}, default=str, sort_keys=True)
    # Rows are immutable snapshots owned by the serialized checkpoint writer.
    # Re-encode only a replaced row; keep the existing JSON schema and atomic
    # full-file replacement, without walking every nested result on every save.
    payload = header[:-1] + ',"ticker_results":{' + ','.join(
        json.dumps(key) + ':' + cache[key][1] for key in sorted(rows)) + '}}'
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    from pcs.data.access import _atomic_replace_with_retry
    _atomic_replace_with_retry(temporary, path)


def run_pcs_pool(*, universe_id: str | None = None, symbols: Sequence[str] | None = None,
                 as_of: datetime | str = "latest",
                 mode: Literal["PREMARKET", "INTRADAY", "EOD"],
                 data_mode: Literal["PREPARE_THEN_SCAN", "READ_ONLY"] = "READ_ONLY",
                 strategies: Sequence[str] | None = None,
                 event_policy: Literal["HOLD_TO_EXPIRY", "PLANNED_EARLY_EXIT"] = "HOLD_TO_EXPIRY",
                 planned_exit_before_event_sessions: int | None = None,
                 max_workers: int = 8, output_directory=None,
                 data_access: PCSDataAccess | None = None,
                 benchmark_symbol: str = "QQQ", options_reader=None,
                 option_rules=None, event_status_reader=None,
                 portfolio_status_reader=None, static_metadata_reader=None,
                 contract_selector=None, market_state_reader=None,
                 portfolio_context_reader=None, event_calendar_reader=None,
                 daily_handle_resolver=None, auto_prepare_data=False,
                 refresh_policy="INCREMENTAL_IF_NEEDED", max_data_workers=4,
                 max_scan_workers=None, stage_timeout_seconds: float | None = 60.0,
                 timeout_seconds: float | None = None, baseline_run_id: str | None = None,
                 recovery_run_id: str | None = None, resume: bool = True,
                 new_run: bool = False, resume_run_id: str | None = None,
                 checkpoint_callback=None, evidence_window: int = 60,
                 scope: str = 'PRODUCTION', observation_input=None,
                 observation_adapter=None) -> PoolScanResult:
    """Scan pinned daily/options inputs; preparation requires explicit opt-in."""
    if scope=='STOCK_OBSERVATION':
        if observation_input is None or data_mode!='READ_ONLY' or auto_prepare_data:
            raise ValueError('OBSERVATION_READ_ONLY_SPEC_REQUIRED')
        return _run_stock_observation(observation_input,data_access=data_access,
            adapter=observation_adapter,checkpoint_callback=checkpoint_callback)
    if scope!='PRODUCTION' or observation_input is not None:
        raise ValueError('POOL_SCOPE_INVALID')
    if mode not in {"PREMARKET", "INTRADAY", "EOD"}:
        raise ValueError("mode must be PREMARKET, INTRADAY, or EOD")
    if data_mode not in {"PREPARE_THEN_SCAN", "READ_ONLY"}:
        raise ValueError("data_mode must be PREPARE_THEN_SCAN or READ_ONLY")
    if data_mode == "PREPARE_THEN_SCAN" and not auto_prepare_data:
        raise ValueError("AUTO_PREPARE_DATA_REQUIRED")
    if data_mode == "READ_ONLY" and auto_prepare_data:
        raise ValueError("AUTO_PREPARE_DATA_REQUIRES_PREPARE_THEN_SCAN")
    from pcs.data.massive_client import load_project_environment
    load_project_environment()
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    if max_data_workers < 1:
        raise ValueError("max_data_workers must be positive")
    if not isinstance(evidence_window, int) or evidence_window <= 0:
        raise ValueError("evidence_window must be a positive integer")
    if stage_timeout_seconds is not None and timeout_seconds is not None:
        raise ValueError("specify only one timeout")
    stage_timeout_seconds = timeout_seconds if timeout_seconds is not None else stage_timeout_seconds
    if stage_timeout_seconds is None:
        stage_timeout_seconds = 60.0
    if not isfinite(stage_timeout_seconds) or stage_timeout_seconds <= 0:
        raise ValueError("stage_timeout_seconds must be finite and positive")
    if event_policy not in {"HOLD_TO_EXPIRY", "PLANNED_EARLY_EXIT"}:
        raise ValueError("unsupported event policy")
    if event_policy == "PLANNED_EARLY_EXIT" and (planned_exit_before_event_sessions is None or planned_exit_before_event_sessions < 1):
        raise ValueError("planned early exit requires positive exit buffer sessions")
    spec = resolve_pool_universe(symbols, universe_id)
    run_id = uuid.uuid4().hex
    asof = _as_of(as_of)
    if hasattr(contract_selector, "bind_run"):
        contract_selector.bind_run(asof, mode)
    effective = resolve_effective_market_session(asof, mode, "XNYS")
    effective_asof = str(effective.date())
    from .artifacts import ProgressCheckpoint
    progress = ProgressCheckpoint(output_directory, run_id, metadata={
        "as_of": asof, "mode": mode, "universe_id": spec.universe_id,
        "universe_count": len(spec.symbols), "effective_daily_session": effective_asof,
    })
    progress.update(stage="READINESS_AUDIT")
    access = data_access or PCSDataAccess()
    started = perf_counter()
    daily_resolver = daily_handle_resolver or resolve_active_verified_daily_handle
    if option_rules is None:
        option_rules = load_pool_option_rules()
    stage_latency: dict[str, float] = {}
    audit_started = perf_counter()
    dependencies = tuple(dict.fromkeys((*[str(s).strip().upper() for s in spec.symbols],
                                        str(benchmark_symbol).strip().upper())))
    runtime = PoolRuntime(access=access, run_id=run_id, as_of=asof,
                          telemetry=output_directory is not None, total=len(spec.symbols),
                          stage_timeout_seconds=stage_timeout_seconds,
                          daily_handle_resolver=daily_resolver,
                          options_handle_resolver=(resolve_active_verified_options_handle
                                                   if mode == "EOD" and options_reader is None else None))
    # Establish the recovery anchor before the potentially expensive shared
    # readiness audit.  This guarantees an interrupted audit leaves an
    # identity-bound checkpoint, even when no ticker has reached scan yet.
    candidate_resume_enabled = resume
    checkpoint_path = None
    checkpoint_identity = None
    checkpoint_rows: dict[str, TickerScanResult] = {}
    checkpoint_encoded_rows = {}
    if output_directory is not None:
        snapshot_seed = PoolRunSnapshot(
            run_id, asof, mode, effective_asof,
            f"{spec.universe_id}:{spec.version}:{spec.universe_role}:{len(spec.symbols)}:{spec.fingerprint}",
            manifest_snapshot_id=runtime.manifest_snapshot_id,
            requested_as_of=asof, effective_daily_session=effective_asof,
            benchmark_status="PENDING")
        config_root = Path("config")
        config_identity = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in sorted(config_root.rglob("*.yaml"))}
        checkpoint_identity = _checkpoint_identity(snapshot_seed, option_rules, context={
            "symbols": list(spec.symbols), "benchmark": benchmark_symbol,
            "config": config_identity, "refresh_policy": refresh_policy,
            "data_mode": data_mode, "event_policy": event_policy, "evidence_window": evidence_window,
            "strategies": strategies, "exit_buffer": planned_exit_before_event_sessions,
            "parquet_root": str(getattr(access, "parquet_root", "")),
        })
        # Injected readers may contain live state with no persistent identity.
        # Never reuse their prior decisions merely because the callable is the same.
        if any(callback is not None for callback in (options_reader, event_status_reader,
                portfolio_status_reader, static_metadata_reader, contract_selector,
                market_state_reader, portfolio_context_reader, event_calendar_reader)):
            resume = False
        checkpoint_path = Path(output_directory) / ".checkpoints" / f"{checkpoint_identity}.json"
        prior_run_id, checkpoint_rows = _load_scan_checkpoint(checkpoint_path, checkpoint_identity) if resume else ("", {})
        if resume_run_id and prior_run_id != resume_run_id:
            raise ValueError("CHECKPOINT_IDENTITY_MISMATCH")
        if prior_run_id and not new_run:
            run_id = prior_run_id
            runtime.run_id = run_id
            snapshot_seed = replace(snapshot_seed, run_id=run_id)
        _write_scan_checkpoint(checkpoint_path, identity=checkpoint_identity, run_id=run_id,
                               snapshot=snapshot_seed, rows=checkpoint_rows, stage="READINESS_AUDIT",
                               status="IN_PROGRESS", encoded_rows=checkpoint_encoded_rows)
        if checkpoint_callback is not None:
            checkpoint_callback(str(checkpoint_path), checkpoint_identity)
    progress = ProgressCheckpoint(output_directory, run_id, metadata={
        "as_of": asof, "mode": mode, "universe_id": spec.universe_id,
        "universe_count": len(spec.symbols), "effective_daily_session": effective_asof,
    })
    initial = _audit_verified_daily(
        runtime.observe("", "manifest_index_preflight", lambda: _daily_preflight(
            dependencies, access, effective_asof, runtime.manifest_snapshot)),
        dependencies, access, effective_asof, daily_resolver, runtime=runtime,
        max_workers=max_workers, timeout_seconds=stage_timeout_seconds)
    stage_latency["readiness_audit"] = (perf_counter() - audit_started) * 1000
    if runtime.manifest_snapshot is not None:
        runtime.manifest_snapshot.assert_current()
    prep_required = tuple(symbol for symbol in dependencies
                          if initial[symbol].status == "PREP_REQUIRED")
    hard_blocked = {symbol: initial[symbol] for symbol in dependencies
                    if initial[symbol].status == "HARD_BLOCKED"}
    prep_results = {}
    prep_counters = {"attempted": 0, "provider_calls": 0, "promotion_calls": 0}
    if data_mode == "PREPARE_THEN_SCAN" and prep_required:
        prep_started = perf_counter()
        prep_results, prep_counters = _bounded_daily_preparation(
            prep_required, access, effective_asof,
            max_workers=max_data_workers, timeout_seconds=stage_timeout_seconds)
        runtime.refresh_manifest_snapshot()
        stage_latency["daily_preparation"] = (perf_counter() - prep_started) * 1000
    else:
        stage_latency["daily_preparation"] = 0.0
    # Never revalidate a timed-out writer while it may still be completing a transaction.
    revalidation_symbols = tuple(symbol for symbol in prep_required
                                 if prep_results.get(symbol, {}).get("result_status") != "TIMEOUT") \
        if data_mode == "PREPARE_THEN_SCAN" else ()
    revalidated = (_revalidate_daily(revalidation_symbols, access, effective_asof, daily_resolver)
                   if revalidation_symbols else {})
    prepared_ready = {symbol for symbol in revalidation_symbols
                      if revalidated.get(symbol, DailyReadiness("HARD_BLOCKED")).status == "READY"}
    stage_latency["readiness_revalidation"] = 0.0
    prep_evidence = {}
    for symbol in dependencies:
        initial_state = initial[symbol]
        result = prep_results.get(symbol)
        if initial_state.status == "READY":
            prep_evidence[symbol] = {"status": "NOT_NEEDED", "reasons": (), "attempted": False,
                                     "result_status": "ALREADY_COMPLETE", "dataset": "daily"}
        elif initial_state.status == "HARD_BLOCKED":
            prep_evidence[symbol] = {"status": "HARD_BLOCKED",
                                     "reasons": initial_state.reason_codes, "attempted": False,
                                     "result_status": "NOT_RUN", "dataset": "daily"}
        elif data_mode == "READ_ONLY":
            prep_evidence[symbol] = {"status": "READ_ONLY_NOT_PREPARED",
                                     "reasons": initial_state.reason_codes, "attempted": False,
                                     "result_status": "NOT_RUN", "dataset": "daily"}
        elif symbol in prepared_ready:
            prep_evidence[symbol] = {"status": "PREPARED_READY",
                                     "reasons": tuple(result.get("reason_codes", ()) if result else ()),
                                     "attempted": True,
                                     "result_status": result.get("result_status", "READY") if result else "READY",
                                     "dataset": "daily"}
        else:
            reasons = tuple(dict.fromkeys((*((result or {}).get("reason_codes", ())),
                                           *(revalidated.get(symbol, initial_state).reason_codes))))
            prep_evidence[symbol] = {"status": "HARD_BLOCKED" if symbol in hard_blocked else "PREPARATION_FAILED",
                                     "reasons": reasons or ("DAILY_PREPARATION_FAILED",),
                                     "attempted": True,
                                     "result_status": (result or {}).get("result_status", "FAILED"),
                                     "dataset": "daily"}

    scan_ready = {symbol for symbol in dependencies
                  if (initial[symbol].status == "READY" or symbol in prepared_ready)}
    benchmark_blocker = None
    if benchmark_symbol.upper() in hard_blocked:
        benchmark_blocker = "BENCHMARK_HARD_BLOCKED"
    elif benchmark_symbol.upper() not in scan_ready:
        benchmark_blocker = "BENCHMARK_PREPARATION_FAILED" if data_mode == "PREPARE_THEN_SCAN" else "BENCHMARK_PREP_REQUIRED"

    options_resolver = (resolve_active_verified_options_handle
                        if mode == "EOD" and options_reader is None else None)
    # The runtime was created before the readiness audit so verified daily
    # handles are shared by audit, benchmark, and ticker stages.
    benchmark = None
    progress.update(stage="BENCHMARK")
    benchmark_started = perf_counter()
    if benchmark_blocker is None:
        try:
            benchmark_handle = runtime.resolve_daily(benchmark_symbol, effective_asof, 200,
                                                     resolver=daily_resolver)
            benchmark = runtime.read_daily(benchmark_handle, end_date=effective_asof,
                                           required_warmup_rows=200)
        except Exception:
            benchmark_blocker = "BENCHMARK_VERIFIED_READ_FAILED"
    stage_latency["benchmark"] = (perf_counter() - benchmark_started) * 1000
    completed = effective if benchmark is not None else None
    if benchmark is not None and completed is not None:
        benchmark = benchmark[pd.to_datetime(benchmark["date"]).dt.normalize() <= completed].copy()
    code_revision, engine_version = _execution_identity(option_rules)
    snapshot = PoolRunSnapshot(
        run_id, asof, mode,
        str(completed.date()) if completed is not None else None,
        f"{spec.universe_id}:{spec.version}:{spec.universe_role}:{len(spec.symbols)}:{spec.fingerprint}",
        benchmark_handles={benchmark_symbol: "PINNED" if benchmark is not None else "UNAVAILABLE"},
        code_revision=code_revision, engine_version=engine_version,
        manifest_snapshot_id=runtime.manifest_snapshot_id,
        benchmark_status="READY" if benchmark is not None else (benchmark_blocker or "BLOCKED"))
    snapshot = PoolRunSnapshot(**{**snapshot.__dict__, "requested_as_of": asof,
                                  "effective_daily_session": effective_asof})
    progress.update(snapshot=asdict(snapshot))
    preflight_results = {}
    queued_symbols = []
    for symbol in spec.symbols:
        normalized = str(symbol).strip().upper()
        evidence = prep_evidence[normalized]
        if benchmark_blocker is not None:
            preflight_results[normalized] = TickerScanResult(
                normalized, run_id, asof, EligibilityStatus.DATA_BLOCKED,
                final_action=FinalAction.DATA_FAILED,
                reason_codes=(benchmark_blocker,),
                preparation_status=evidence["status"],
                preparation_reason_codes=tuple(evidence["reasons"]),
                preparation_attempted=evidence["attempted"],
                preparation_result_status=evidence["result_status"],
                prepared_dataset=evidence["dataset"],
                effective_daily_session=effective_asof,
                initial_daily_readiness=initial[normalized].status)
            progress.save(preflight_results[normalized])
        elif normalized not in scan_ready:
            preflight_results[str(symbol).upper()] = TickerScanResult(
                normalized, run_id, asof, EligibilityStatus.DATA_BLOCKED,
                final_action=FinalAction.TEMP_BLOCKED if evidence["status"] != "HARD_BLOCKED" else FinalAction.DATA_FAILED,
                reason_codes=tuple(evidence["reasons"]),
                preparation_status=evidence["status"],
                preparation_reason_codes=tuple(evidence["reasons"]),
                preparation_attempted=evidence["attempted"],
                preparation_result_status=evidence["result_status"],
                prepared_dataset=evidence["dataset"],
                effective_daily_session=effective_asof,
                initial_daily_readiness=initial[normalized].status)
            progress.save(preflight_results[normalized])
        else:
            queued_symbols.append(symbol)
    if output_directory is not None:
        prior_run_id, prior_rows = _load_scan_checkpoint(checkpoint_path, checkpoint_identity) if resume else ("", {})
        if prior_run_id:
            run_id = prior_run_id
            snapshot = replace(snapshot, run_id=run_id)
            checkpoint_rows = {}
            for symbol, row in prior_rows.items():
                if symbol not in queued_symbols or any(code in row.reason_codes for code in
                        ("WORKER_TIMEOUT", "WORKER_FAILED", "STAGE_DEADLINE_NOT_STARTED", "DAILY_TIMING_FAILED")):
                    continue
                daily_identity = runtime.input_identity(symbol, benchmark_symbol)
                if row.daily_input_identity != daily_identity:
                    continue
                identity = runtime.input_identity(symbol, benchmark_symbol,
                    options=row.timing_status == TimingStatus.TIMING_ENTRY_READY)
                if row.input_identity == identity:
                    checkpoint_rows[symbol] = row
                elif row.stage_state:
                    checkpoint_rows[symbol] = replace(row, checkpoint_stage="OPTIONS_PENDING")
            for symbol in tuple(spec.symbols):
                normalized = str(symbol).strip().upper()
                if normalized in checkpoint_rows and normalized in queued_symbols and checkpoint_rows[normalized].checkpoint_stage == "COMPLETE":
                    queued_symbols.remove(symbol)
                if normalized in checkpoint_rows and checkpoint_rows[normalized].checkpoint_stage == "COMPLETE":
                    checkpoint_rows[normalized] = replace(
                        checkpoint_rows[normalized], run_id=run_id,
                        cache_hits=tuple(dict.fromkeys((*checkpoint_rows[normalized].cache_hits,
                                                        "CHECKPOINT:DAILY_TIMING"))))
                    preflight_results.pop(normalized, None)
        checkpoint_rows.update(preflight_results)
        _write_scan_checkpoint(checkpoint_path, identity=checkpoint_identity, run_id=run_id,
                               snapshot=snapshot, rows=checkpoint_rows,
                               stage="PREFLIGHT", status="IN_PROGRESS", encoded_rows=checkpoint_encoded_rows)

    checkpoint_lock = RLock()
    accepting_results = True

    def save_scan_outcome(outcome):
        with checkpoint_lock:
            if accepting_results:
                save_locked(outcome)

    def save_locked(outcome):
        if checkpoint_path is None:
            return
        if outcome.value is not None:
            checkpoint_rows[outcome.symbol] = replace(outcome.value,
                daily_input_identity=runtime.input_identity(outcome.symbol, benchmark_symbol),
                input_identity=runtime.input_identity(outcome.symbol, benchmark_symbol,
                    options=outcome.value.timing_status == TimingStatus.TIMING_ENTRY_READY))
        else:
            checkpoint_rows[outcome.symbol] = TickerScanResult(
                outcome.symbol, run_id, asof, EligibilityStatus.DATA_BLOCKED,
                final_action=FinalAction.DATA_FAILED, reason_codes=outcome.reason_codes)
        runtime.observe(outcome.symbol, "save_result", lambda: _write_scan_checkpoint(
            checkpoint_path, identity=checkpoint_identity, run_id=run_id,
            snapshot=snapshot, rows=checkpoint_rows, stage="DAILY_TIMING", status="IN_PROGRESS",
            encoded_rows=checkpoint_encoded_rows))
        runtime.processed = sum(row.checkpoint_stage == "COMPLETE" for row in checkpoint_rows.values())
        runtime.last_result_saved_at = datetime.now(timezone.utc).isoformat()
        print(json.dumps({"status": "POOL_SCAN_PROGRESS", "run_id": run_id,
                          "processed": runtime.processed, "remaining": len(spec.symbols)-runtime.processed,
                          "total": len(spec.symbols),
                          "current_symbol": outcome.symbol, "stage": "DAILY_TIMING",
                          "last_result_saved_at": datetime.now(timezone.utc).isoformat()},
                         sort_keys=True), file=__import__("sys").stderr, flush=True)

    from .artifacts import CandidateCheckpoints
    checkpoints = CandidateCheckpoints(output_directory, run_id)
    checkpoint_identities = {}

    resume_enabled = candidate_resume_enabled

    def scan_symbol(symbol):
        resume = None
        if mode == "EOD" and options_reader is None and benchmark is not None:
            try:
                handle = runtime.resolve_daily(symbol, effective_asof, 200, resolver=daily_resolver)
                identity = {"symbol": symbol, "session": effective_asof, "mode": mode,
                    "code_revision": code_revision, "rules_identity": engine_version,
                    "daily": list(runtime._handle_key(handle)),
                    "daily_manifest": str(getattr(handle, "manifest_identity", "")),
                    "benchmark": list(runtime._handle_key(benchmark_handle)),
                    "benchmark_manifest": str(getattr(benchmark_handle, "manifest_identity", "")),
                    "static_metadata": (static_metadata_reader(symbol) if static_metadata_reader else None)}
                checkpoint_identities[symbol] = identity
                # Missing immutable identity is never compatible evidence.
                if getattr(handle, "checksum", None) and getattr(handle, "generation_id", None):
                    if resume_enabled:
                        resume = checkpoints.resume(symbol, identity)
            except Exception:
                # The canonical evaluator owns the fail-closed reason code;
                # checkpoint lookup must never mask it as WORKER_FAILED.
                pass
        if resume is not None:
            result = replace(resume, run_id=run_id, as_of=asof, event_status="NOT_EVALUATED",
                portfolio_status="NOT_EVALUATED", final_action=FinalAction.WAIT,
                checkpoint_stage="OPTIONS_PENDING",
                candidate_state={**resume.candidate_state, "timing_reused": True})
            with checkpoint_lock:
                if accepting_results:
                    progress.save(result)
            return result
        result = _evaluate_symbol(symbol, run_id=run_id, asof=asof, access=access,
            runtime=runtime, benchmark=benchmark, benchmark_symbol=benchmark_symbol,
            options_reader=options_reader, option_rules=option_rules, daily_asof=effective_asof,
            static_metadata_reader=static_metadata_reader,
            daily_handle_resolver=daily_handle_resolver, auto_prepare_data=False,
            refresh_policy=refresh_policy, options_prepare=None,
            options_enabled=(options_reader is not None), mode=mode,
            contract_selector=contract_selector, market_state_reader=market_state_reader,
            portfolio_context_reader=portfolio_context_reader, event_calendar_reader=event_calendar_reader,
            evidence_window=evidence_window,
            saved_stage=(checkpoint_rows.get(symbol) if checkpoint_rows.get(symbol) is not None
                         and checkpoint_rows[symbol].checkpoint_stage != "COMPLETE" else None),
            on_stage=lambda row: save_scan_outcome(SimpleNamespace(symbol=row.symbol, value=row)))
        if mode == "EOD" and options_reader is None and result.timing_status == TimingStatus.TIMING_ENTRY_READY:
            result = replace(result, checkpoint_stage="OPTIONS_PENDING")
        with checkpoint_lock:
            if accepting_results:
                progress.save(result)
        return result

    progress.update(stage="SCAN")
    scan = runtime.run_stage(tuple(queued_symbols), scan_symbol,
        stage_name="scan", max_workers=(max_scan_workers or max_workers), timeout_seconds=stage_timeout_seconds,
        on_outcome=save_scan_outcome)
    with checkpoint_lock:
        accepting_results = False
    if runtime.manifest_snapshot is not None:
        runtime.manifest_snapshot.assert_current()
    stage_latency["scan"] = runtime.stage_latency_ms.get("scan", 0.0)
    outcomes = scan.outcomes
    worker_results = {outcome.symbol: (outcome.value if outcome.value is not None else TickerScanResult(
        outcome.symbol, run_id, asof, EligibilityStatus.DATA_BLOCKED,
        final_action=FinalAction.DATA_FAILED, reason_codes=outcome.reason_codes))
        for outcome in outcomes}
    worker_results.update({symbol: row for symbol, row in checkpoint_rows.items()
                           if symbol not in worker_results and symbol not in preflight_results})
    progress.update(stage="OPTIONS")
    options_started = perf_counter()
    options_deadline = options_started + stage_timeout_seconds
    options_preparation_results = {}
    for symbol, row in tuple(worker_results.items()):
        if mode != "EOD" or options_reader is not None or row.timing_status != TimingStatus.TIMING_ENTRY_READY:
            continue
        if not row.candidate_state:
            # A custom scanner without recovery evidence cannot be resumed.
            continue
        row = replace(row, candidate_state={**row.candidate_state,
            "code_identity": code_revision, "rules_identity": engine_version,
            "market_session": effective_asof, "decision_as_of": asof})
        if not row.candidate_state.get("preparation_receipt"):
            evidence, next_review = checkpoints.preparation_evidence(symbol, asdict(_options_requirement(row, option_rules)))
            row = replace(row, candidate_state={**row.candidate_state, **evidence}, next_review_at=next_review)
        def save(candidate):
            identity = checkpoint_identities.get(symbol)
            if identity:
                checkpoints.save(candidate, identity)
            with checkpoint_lock:
                save_locked(SimpleNamespace(symbol=symbol, value=replace(candidate, checkpoint_stage="OPTIONS_PENDING")))
        def evaluate(candidate):
            return _evaluate_symbol(symbol, run_id=run_id, asof=asof, access=access,
                runtime=runtime, benchmark=benchmark, benchmark_symbol=benchmark_symbol,
                options_reader=None, option_rules=option_rules, daily_asof=effective_asof,
                static_metadata_reader=static_metadata_reader, daily_handle_resolver=daily_handle_resolver,
                auto_prepare_data=False, options_enabled=True, mode=mode, resume_row=candidate,
                contract_selector=contract_selector, market_state_reader=market_state_reader,
                portfolio_context_reader=portfolio_context_reader, event_calendar_reader=event_calendar_reader,
                evidence_window=evidence_window)
        try:
            row = _continue_candidate(row, runtime=runtime, rules=option_rules,
                allow_prepare=auto_prepare_data, evaluate=evaluate, save=save, deadline=options_deadline,
                reuse_evaluation=(contract_selector is None and market_state_reader is None
                                  and portfolio_context_reader is None and event_calendar_reader is None))
        except Exception as exc:
            row = replace(row, options_status=OptionsStatus.DATA_BLOCKED, final_action=FinalAction.WAIT,
                reason_codes=tuple(row.candidate_state["timing_reason_codes"]) + (_safe_reason(exc),),
                candidate_state={**row.candidate_state, "resume_stage": "OPTIONS", "options_data_status": "WAITING_DATA"})
            save(row)
        worker_results[symbol] = replace(row, checkpoint_stage="COMPLETE")
        row = worker_results[symbol]
        with checkpoint_lock:
            save_locked(SimpleNamespace(symbol=symbol, value=row))
        progress.save(row)
        if row.candidate_state.get("preparation_receipt") and row.candidate_state.get("preparation_attempt_run_id") == run_id:
            options_preparation_results[symbol] = row.candidate_state["preparation_receipt"]
    stage_latency["options_recovery"] = (perf_counter() - options_started) * 1000
    worker_results = {
        symbol: replace(row,
                        preparation_status=prep_evidence[symbol]["status"],
                        preparation_reason_codes=tuple(prep_evidence[symbol]["reasons"]),
                        preparation_attempted=prep_evidence[symbol]["attempted"],
                        preparation_result_status=prep_evidence[symbol]["result_status"],
                        prepared_dataset=prep_evidence[symbol]["dataset"],
                        effective_daily_session=effective_asof,
                        initial_daily_readiness=initial[symbol].status)
        for symbol, row in worker_results.items()
    }
    results = []
    for symbol in spec.symbols:
        normalized = str(symbol).upper()
        results.append(preflight_results[normalized] if normalized in preflight_results
                       else worker_results[normalized])
    summary = {"raw_count": len(spec.symbols),
               "hard_excluded_count": sum(r.eligibility_status == EligibilityStatus.HARD_EXCLUDED for r in results),
               "data_blocked_count": sum(r.eligibility_status == EligibilityStatus.DATA_BLOCKED for r in results),
               "pcs_eligible_count": sum(r.eligibility_status == EligibilityStatus.PCS_ELIGIBLE for r in results),
               "dormant_count": sum(r.timing_status == TimingStatus.DORMANT for r in results),
               "timing_watch_count": sum(r.timing_status == TimingStatus.WATCH for r in results),
               "timing_entry_ready_count": sum(r.timing_status == TimingStatus.TIMING_ENTRY_READY for r in results),
               "options_check_count": 0,
               "spread_count": sum(r.spread_count for r in results),
               "pcs_trade_ready_count": 0,
               "temp_blocked_count": sum(r.final_action == FinalAction.TEMP_BLOCKED for r in results),
               "rejected_count": sum(r.final_action == FinalAction.REJECTED for r in results),
               "missing_ticker_decisions": len(spec.symbols)-len(results),
               "daily_ready_initial_count": sum(initial[s].status == "READY" for s in dependencies),
               "daily_prep_required_count": sum(initial[s].status == "PREP_REQUIRED" for s in dependencies),
               "daily_hard_blocked_initial_count": sum(initial[s].status == "HARD_BLOCKED" for s in dependencies),
               "daily_prepare_attempted_count": prep_counters["attempted"],
               "daily_provider_coverage_count": prep_counters.get("provider_coverage_count", 0),
               "daily_prepared_ready_count": sum(prep_evidence[s]["status"] == "PREPARED_READY" for s in dependencies),
               "daily_prepare_failed_count": sum(prep_evidence[s]["status"] == "PREPARATION_FAILED" for s in dependencies),
               "daily_scan_ready_count": sum(s in scan_ready for s in spec.symbols),
               "benchmark_daily_prepare_attempted": int(prep_evidence[benchmark_symbol.upper()]["attempted"]),
               "benchmark_blocked": int(benchmark_blocker is not None)}
    if options_reader is not None or options_resolver is not None:
        summary["options_check_count"] = sum(row.options_status != OptionsStatus.NOT_EVALUATED for row in results)
    if event_status_reader is not None or portfolio_status_reader is not None:
        from .final_gates import finalize_ticker_result
        by_symbol = {row.symbol: row for row in results}

        def finalize(symbol):
            row = by_symbol[symbol]
            event_status = event_status_reader(row.symbol, row) if event_status_reader is not None else "EVENT_DATA_STALE"
            portfolio_status = portfolio_status_reader(row.symbol, row) if portfolio_status_reader is not None else "PORTFOLIO_DATA_STALE"
            return finalize_ticker_result(row, event_status=event_status,
                                          portfolio_status=portfolio_status)

        final_stage = runtime.run_stage(
            tuple(by_symbol), finalize, stage_name="finalize",
            max_workers=(max_scan_workers or max_workers), timeout_seconds=stage_timeout_seconds)
        stage_latency["finalize"] = runtime.stage_latency_ms.get("finalize", 0.0)
        results = [outcome.value if outcome.value is not None else by_symbol[outcome.symbol]
                   for outcome in final_stage.outcomes]
        summary["pcs_trade_ready_count"] = sum(row.final_action == FinalAction.PCS_TRADE_READY for row in results)
    else:
        stage_latency["finalize"] = 0.0
    for row in results:
        if row.symbol in checkpoint_identities and row.timing_status == TimingStatus.TIMING_ENTRY_READY:
            checkpoints.save(row, checkpoint_identities[row.symbol])
    counters = {
        "ordinary_reader_calls": 0,
        "options_reader_calls": summary["options_check_count"],
        "provider_calls": prep_counters["provider_calls"],
        "promotion_calls": prep_counters["promotion_calls"],
        "recovery_calls": prep_counters["attempted"],
        "handle_resolution_calls": runtime.counters.get("handle_resolution_calls", 0),
        "daily_frame_reads": runtime.counters.get("daily_frame_reads", 0),
        "options_frame_reads": runtime.counters.get("options_frame_reads", 0),
    }
    preparation_results = {
        symbol: _serialize_preparation_result(prep_results[symbol])
        for symbol in prep_results
        if isinstance(prep_results.get(symbol), Mapping)
    }
    for symbol, receipt in options_preparation_results.items():
        preparation_results.setdefault(symbol, {})["options"] = receipt
    summary["options_verified_count"] = sum(r.candidate_state.get("verified_read_status") == "PASS" for r in results)
    summary["options_evaluated_count"] = sum(r.options_status in {OptionsStatus.PASS, OptionsStatus.DISCOVERED, OptionsStatus.REJECT} for r in results)
    summary["options_prepare_attempted_count"] = sum(bool(r.get("control_plane_attempted", r.get("provider_attempted"))) for r in options_preparation_results.values())
    summary["timing_reused_count"] = sum(bool(r.candidate_state.get("timing_reused")) for r in results)
    counters["options_prepare_attempted"] = summary["options_prepare_attempted_count"]
    counters["provider_calls"] += sum(bool(r.get("provider_attempted")) for r in options_preparation_results.values())
    counters["promotion_calls"] += sum(len(r.get("promoted_partitions", ())) for r in options_preparation_results.values())
    counters["recovery_calls"] += summary["options_prepare_attempted_count"]
    recovery_summary = summarize_recovery_results(prep_results)
    stage_latency["validation"] = 0.0
    from .validation import validate_pool_result
    validation_started = perf_counter()
    summary["run_status"] = ("PARTIAL_TIMEOUT" if any("WORKER_TIMEOUT" in r.reason_codes or "STAGE_DEADLINE_NOT_STARTED" in r.reason_codes for r in results)
                              else "COMPLETED_NO_EVALUABLE_TICKERS" if not any(r.timing_status != TimingStatus.NOT_EVALUATED for r in results)
                              else "COMPLETED")
    summary["timeout_count"] = sum("WORKER_TIMEOUT" in r.reason_codes for r in results)
    summary["unprocessed_count"] = sum("STAGE_DEADLINE_NOT_STARTED" in r.reason_codes for r in results)
    summary["execution_failed_count"] = sum(any(c in r.reason_codes for c in
        ("WORKER_FAILED", "DAILY_TIMING_FAILED")) for r in results)
    summary["blocked_assessment_count"] = sum(
        (r.eligibility_status == EligibilityStatus.DATA_BLOCKED or r.options_status == OptionsStatus.DATA_BLOCKED
         or "TIMING_EVIDENCE_UNAVAILABLE" in r.reason_codes)
        and not any(c in r.reason_codes for c in ("WORKER_FAILED", "DAILY_TIMING_FAILED", "WORKER_TIMEOUT", "STAGE_DEADLINE_NOT_STARTED"))
        for r in results)
    counters["checkpoint_hits"] = sum(bool(r.cache_hits) for r in results)
    result = PoolScanResult(snapshot, tuple(results), summary,
                            stage_latency_ms=stage_latency, counters=counters,
                            discovered_contracts=tuple(
                                candidate for row in results
                                for candidate in row.discovered_contracts),
                            preparation_results=preparation_results,
                            recovery_summary=recovery_summary)
    validate_pool_result(result, spec.symbols)
    stage_latency["validation"] = (perf_counter() - validation_started) * 1000
    stage_latency["total"] = (perf_counter() - started) * 1000
    stage_latency.update({f"operation:{key}": value for key, value in runtime.stage_latency_ms.items()
                          if key != "scan"})
    result = PoolScanResult(snapshot, tuple(results), summary,
                            stage_latency_ms=stage_latency, counters=counters,
                            discovered_contracts=tuple(
                                candidate for row in results
                                for candidate in row.discovered_contracts),
                            preparation_results=preparation_results,
                            recovery_summary=recovery_summary)
    if checkpoint_path is not None:
        final_rows = {row.symbol: (checkpoint_rows[row.symbol]
                      if row.symbol in checkpoint_rows and "WORKER_TIMEOUT" in row.reason_codes
                      else replace(row,
                          daily_input_identity=runtime.input_identity(row.symbol, benchmark_symbol),
                          input_identity=runtime.input_identity(row.symbol, benchmark_symbol,
                              options=row.timing_status == TimingStatus.TIMING_ENTRY_READY)))
                      for row in result.ticker_results}
        _write_scan_checkpoint(checkpoint_path, identity=checkpoint_identity, run_id=run_id,
                               snapshot=snapshot, rows=final_rows,
                               stage="DAILY_TIMING", status="COMPLETE" if summary["run_status"] == "COMPLETED" else "PARTIAL",
                               encoded_rows=checkpoint_encoded_rows)
    if output_directory is not None:
        progress.update(stage="PERSISTING")
        from .artifacts import persist_pool_artifacts
        persist_pool_artifacts(result, output_directory, baseline_run_id=baseline_run_id,
                               recovery_run_id=recovery_run_id, evidence_window=evidence_window)
        progress.update(stage="COMPLETE", completed=True)
    runtime.close()
    return result


__all__ = ["run_pcs_pool", "reconcile_pool_scan_results", "summarize_recovery_results"]
