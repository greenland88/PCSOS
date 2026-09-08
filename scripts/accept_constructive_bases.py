"""Verify final Step 7 saved artifacts and reconcile old family identities."""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
from collections import Counter

from pcs.pool.artifacts import _write_atomic
from pcs.pool.opportunities import read_opportunity_bundle, opportunity_to_ai_view, opportunities_to_markdown, _csv_view
from pcs.trend.constructive_base import detect_constructive_base
from pcs.trend.opportunity_engine import evaluate_entry_opportunity
from pcs.trend.selection_models import EntryOpportunity, OpportunityInput, ConstructiveBaseInput, BaseResult
from pcs.validation import ValidationRun


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_directory')
    parser.add_argument('--baseline',default='H:/workspace/PCSOS/selection_v2_outputs/step_06_r1_r5_20260908')
    args=parser.parse_args()
    root=Path(args.output_directory); code=Path(__file__).resolve().parents[1]
    guard=ValidationRun(code,tuple((code/'src/pcs/trend').glob('*.py'))+
        (Path(__file__),code/'src/pcs/pool/opportunities.py',root/'artifact_manifest.json',Path(args.baseline)/'artifact_manifest.json'))
    manifest,docs=read_opportunity_bundle(root)
    baseline_manifest,old=read_opportunity_bundle(args.baseline)
    assert manifest['tracked_source_dirty'] is False
    results=[EntryOpportunity.model_validate(x) for x in docs['entry_opportunities.json']]
    inputs={x['call_context']['symbol']:OpportunityInput.model_validate(x) for x in docs['prepared_opportunity_inputs.json']}
    old_results={x['symbol']:x for x in old['entry_opportunities.json']}
    old_inputs={x['call_context']['symbol']:x for x in old['prepared_opportunity_inputs.json']}
    assert len(results)==7
    assert {r.symbol for r in results}==set(old_results)
    assert docs['read_audit.json']['failures']==old['read_audit.json']['failures']
    assert json.loads((root/'entry_opportunities.ai.json').read_text(encoding='utf-8'))==[opportunity_to_ai_view(r) for r in results]
    assert (root/'entry_opportunities.zh-CN.md').read_text(encoding='utf-8')==opportunities_to_markdown(results)
    assert (root/'entry_opportunities.csv').read_text(encoding='utf-8').splitlines()==_csv_view(results).splitlines()
    report=[]
    for result in results:
        inp=inputs[result.symbol]
        assert inp.feature_view.model_dump(mode='json')==old_inputs[result.symbol]['feature_view']
        assert [r.family for r in result.family_results]==['HEALTHY_PULLBACK','SHALLOW_PULLBACK','BREAKOUT_RETEST','CONSTRUCTIVE_BASE']
        assert [r.result_id for r in result.family_results[:3]]==[r['result_id'] for r in old_results[result.symbol]['family_results']]
        without=evaluate_entry_opportunity(inp.model_copy(update={'enabled_families':['HEALTHY_PULLBACK','SHALLOW_PULLBACK','BREAKOUT_RETEST']}))
        assert [r.result_id for r in without.family_results]==[r.result_id for r in result.family_results[:3]]
        base_child=result.family_results[3]; base=base_child.base_result
        single=evaluate_entry_opportunity(inp.model_copy(update={'enabled_families':['CONSTRUCTIVE_BASE']}))
        assert single.result_id==base_child.result_id
        pure=detect_constructive_base(ConstructiveBaseInput(call_context=inp.call_context,feature_view=inp.feature_view,
            effective_policy=inp.base_policy,opportunity_policy=inp.effective_policy,
            structure_evidence=inp.base_structure_evidence,calendar=inp.calendar))
        assert pure.result_id==base.result_id
        assert BaseResult.model_validate_json(base.model_dump_json()).result_id==base.result_id
        cut=base.base_events[0].formed_at if base.base_events else base.timeline[len(base.timeline)//2].session
        context=inp.call_context.model_copy(update={'effective_daily_session':cut,'requested_as_of':cut})
        prefix=evaluate_entry_opportunity(inp.model_copy(update={'call_context':context}))
        prefix=EntryOpportunity.model_validate_json(prefix.model_dump_json())
        restored=evaluate_entry_opportunity(inp.model_copy(update={'prior_family_results':prefix.family_results}))
        same=evaluate_entry_opportunity(inp.model_copy(update={'prior_family_results':result.family_results}))
        assert restored.result_id==same.result_id==result.result_id
        assert prefix.family_results[3].base_result.timeline==base.timeline[:len(prefix.family_results[3].base_result.timeline)]
        candidate=base.formation_candidates[-1] if base.formation_candidates else None
        report.append({'symbol':result.symbol,'checks':'PASS','source_input_unchanged':True,'old_three_child_ids_unchanged':True,
            'candidate_count':len(base.formation_candidates),'formed_count':len(base.base_events),
            'retrospective_test_count':len(base.retrospective_evidence),
            'retrospective_held_count':sum(t.test.status=='HELD' for t in base.retrospective_evidence),
            'first_live_touch_count':sum(e.retest_session is not None for e in base.base_events),
            'live_test_count':sum(len(e.lower_zone.tests) for e in base.base_events),
            'confirmation_count':sum(e.confirmation_session is not None for e in base.base_events),
            'eligible_at_requested_time':base.eligible_at_requested_time,'state':base.opportunity_state,
            'base_detected':base.base_detected,'base_validity':base.base_validity,'result_id':base.result_id,
            'current_missing':[g.model_dump(mode='json') for g in base.current_missing_details],
            'coverage_missing_counts':dict(Counter(g.condition_id for g in base.coverage_missing_evidence)),
            'latest_candidate_failed_or_unknown':[c.model_dump(mode='json') for c in candidate.conditions
                if c.role!='DIAGNOSTIC' and c.predicate_value is not True] if candidate else [],
            'resume_checkpoint':cut,'explanation':base.explanation})
    assert json.loads((root/'base_results.json').read_text(encoding='utf-8'))==[r.family_results[3].base_result.model_dump(mode='json') for r in results]
    read_opportunity_bundle(root); read_opportunity_bundle(args.baseline)
    guard.add_output(root)
    assert guard.finish(root/'acceptance_validation_run.json').value=='VALID'
    _write_atomic(root/'step_07_acceptance.json',json.dumps({'source_commit':manifest['source_commit'],
        'baseline_manifest':baseline_manifest,'canonical_read':False,'provider_read':False,
        'checks':'PASS: hashes, seven unchanged inputs, 21 old child IDs, four views, independent API, single/batch, saved prefix/same-day restore',
        'results':report,'failures':docs['read_audit.json']['failures']},ensure_ascii=False,indent=2))
    print(json.dumps([{k:r[k] for k in ('symbol','candidate_count','formed_count','retrospective_held_count',
        'first_live_touch_count','confirmation_count','state','eligible_at_requested_time','coverage_missing_counts')} for r in report],ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
