"""Bounded R1 re-export: the seven hash-verified Step 7 inputs, platform only."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from pcs.pool.artifacts import _write_atomic
from pcs.pool.opportunities import (read_opportunity_bundle, write_opportunity_artifacts,
    opportunity_to_ai_view, opportunities_to_markdown, _csv_view)
from pcs.trend.constructive_base import detect_constructive_base
from pcs.trend.opportunity_engine import evaluate_entry_opportunity
from pcs.trend.selection_models import OpportunityInput, ConstructiveBaseInput, BaseState, BaseResult
from pcs.validation import ValidationRun

BASELINE_SHA = 'a1282a8d83dc54b955c8241cf963aa5e01284ea1'
SYMBOLS = {'NVDA', 'PLTR', 'MSFT', 'HOOD', 'MDLZ', 'AAL', 'AAOI'}
OLD_FAMILIES = ['HEALTHY_PULLBACK', 'SHALLOW_PULLBACK', 'BREAKOUT_RETEST']


def migrate_input(raw, old_result_id):
    """Explicit input-policy migration, never acceptance of an old checkpoint."""
    if raw['base_policy']['calculation_version'] != 'constructive-base-v1':
        raise ValueError('R1_EXPECTED_V1_INPUT_POLICY')
    if raw['prior_state'] is not None or raw['prior_family_results']:
        raise ValueError('R1_FULL_REPLAY_REQUIRES_NO_PRIOR')
    migrated = deepcopy(raw)
    migrated['base_policy']['calculation_version'] = 'constructive-base-v2'
    migrated['enabled_families'] = ['CONSTRUCTIVE_BASE']
    migrated['base_replay_of_result_id'] = old_result_id
    return OpportunityInput.model_validate(migrated)


def business_values(value):
    """Compare all pre-existing non-identity facts, including nested tests/gates."""
    if isinstance(value, list):
        return [business_values(v) for v in value]
    if isinstance(value, dict):
        return {k:business_values(v) for k,v in value.items()
            if not k.endswith(('_id', '_ids')) and k not in {
                'source_refs', 'evidence_refs', 'reason_codes', 'structure_resolution', 'structure_resolutions'}}
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_directory')
    parser.add_argument('--baseline', default='H:/workspace/PCSOS/selection_v2_outputs/step_07_acceptance_20260908')
    args = parser.parse_args()
    root, baseline = Path(args.output_directory), Path(args.baseline)
    code = Path(__file__).resolve().parents[1]
    guard = ValidationRun(code, tuple((code/'src/pcs/trend').glob('*.py')) +
        (Path(__file__),code/'src/pcs/pool/opportunities.py',baseline/'artifact_manifest.json'))
    if subprocess.check_output(['git','status','--porcelain'],cwd=code,text=True).strip():
        raise ValueError('R1_COMMITTED_CLEAN_SOURCE_REQUIRED')
    manifest, docs = read_opportunity_bundle(baseline)
    assert manifest['source_commit'] == BASELINE_SHA and manifest['tracked_source_dirty'] is False
    old_results = {r['symbol']:r for r in docs['entry_opportunities.json']}
    raw_inputs = docs['prepared_opportunity_inputs.json']
    assert len(raw_inputs) == 7 and {r['call_context']['symbol'] for r in raw_inputs} == SYMBOLS == set(old_results)
    inputs, results, report = [], [], []
    for raw in raw_inputs:
        symbol = raw['call_context']['symbol']
        old_children = old_results[symbol]['family_results']
        assert [r['family'] for r in old_children] == OLD_FAMILIES+['CONSTRUCTIVE_BASE']
        old = next(r['base_result'] for r in old_children if r['family']=='CONSTRUCTIVE_BASE')
        # The reviewed artifacts remain readable as historical raw JSON, but not current typed state.
        try:
            BaseState.model_validate(old['next_state'])
        except ValueError:
            pass
        else:
            raise AssertionError('R1_OLD_CHECKPOINT_ACCEPTED')
        inp = migrate_input(raw, old['result_id'])
        assert inp.feature_view.model_dump(mode='json') == raw['feature_view']
        assert inp.base_structure_evidence == []  # These seven inputs have no optional details.
        result = evaluate_entry_opportunity(inp)
        base = result.base_result
        pure = detect_constructive_base(ConstructiveBaseInput(call_context=inp.call_context,
            feature_view=inp.feature_view,effective_policy=inp.base_policy,opportunity_policy=inp.effective_policy,
            structure_evidence=inp.base_structure_evidence,calendar=inp.calendar,replay_of_result_id=old['result_id']))
        assert pure == base
        assert BaseResult.model_validate_json(base.model_dump_json()) == base
        combined = evaluate_entry_opportunity(inp.model_copy(update={'enabled_families':OLD_FAMILIES+['CONSTRUCTIVE_BASE']}))
        assert combined.family_results[3].result_id == result.result_id
        old_ids = [r['result_id'] for r in old_children[:3]]
        assert [r.result_id for r in combined.family_results[:3]] == old_ids
        cut = base.base_events[0].formed_at if base.base_events else base.timeline[len(base.timeline)//2].session
        ctx = inp.call_context.model_copy(update={'effective_daily_session':cut,'requested_as_of':cut})
        prefix = detect_constructive_base(ConstructiveBaseInput(call_context=ctx,feature_view=inp.feature_view,
            effective_policy=inp.base_policy,opportunity_policy=inp.effective_policy,calendar=inp.calendar))
        restored = detect_constructive_base(ConstructiveBaseInput(call_context=inp.call_context,feature_view=inp.feature_view,
            effective_policy=inp.base_policy,opportunity_policy=inp.effective_policy,calendar=inp.calendar,
            prior_state=BaseState.model_validate_json(prefix.next_state.model_dump_json())))
        assert restored.result_id == base.result_id and restored.next_state == base.next_state
        assert prefix.timeline == base.timeline[:len(prefix.timeline)]
        current = base.model_dump(mode='json')
        compared = ['formation_candidates','base_events','timeline','transitions','retrospective_evidence',
            'base_detected','base_validity','opportunity_state','eligible_at_requested_time','requested_session']
        for key in compared:
            assert business_values(old[key]) == business_values(current[key]), (symbol,key)
        assert old['result_id'] != base.result_id
        assert all(a['candidate_id'] != b.candidate_id for a,b in zip(old['formation_candidates'],base.formation_candidates))
        report.append(dict(symbol=symbol,checks='PASS',feature_view_unchanged=True,
            business_values_unchanged=compared,old_three_child_ids_unchanged=old_ids,
            old_result_id=old['result_id'],new_result_id=base.result_id,
            old_family_result_id=old_children[3]['result_id'],new_family_result_id=result.result_id,
            candidate_count=len(base.formation_candidates),formed_count=len(base.base_events),
            candidate_ids=[dict(session=b.session,old=a['candidate_id'],new=b.candidate_id)
                for a,b in zip(old['formation_candidates'],base.formation_candidates)],
            base_ids=[dict(formed_at=b.formed_at,old=a['base_id'],new=b.base_id,
                old_zone_id=a['lower_zone']['zone_id'],new_zone_id=b.lower_zone.zone_id,
                old_live_test_id=a['live_test_id'],new_live_test_id=b.live_test_id)
                for a,b in zip(old['base_events'],base.base_events)],
            retrospective_held_count=sum(t.test.status=='HELD' for t in base.retrospective_evidence),
            first_live_touch_count=sum(e.retest_session is not None for e in base.base_events),
            confirmation_count=sum(e.confirmation_session is not None for e in base.base_events),
            state=base.opportunity_state,eligible=base.eligible_at_requested_time,resume_checkpoint=cut))
        inputs.append(inp)
        results.append(result)
    audit = dict(input_directory=str(baseline),canonical_read=False,provider_read=False,
        reads=[],historical_failures_not_retried=docs['read_audit.json']['failures'],
        input_policy_migration={'from':'constructive-base-v1','to':'constructive-base-v2',
            'parameters_unchanged':True,'full_replay':True,'families':['CONSTRUCTIVE_BASE']})
    write_opportunity_artifacts(root,results,inputs=inputs,audit=audit)
    output_manifest, output = read_opportunity_bundle(root)
    assert output_manifest['tracked_source_dirty'] is False
    assert all(r['family']=='CONSTRUCTIVE_BASE' and not r['family_results'] for r in output['entry_opportunities.json'])
    assert json.loads((root/'entry_opportunities.ai.json').read_text(encoding='utf-8')) == [opportunity_to_ai_view(r) for r in results]
    assert (root/'entry_opportunities.zh-CN.md').read_text(encoding='utf-8') == opportunities_to_markdown(results)
    assert (root/'entry_opportunities.csv').read_text(encoding='utf-8').splitlines() == _csv_view(results).splitlines()
    final_manifest, _ = read_opportunity_bundle(baseline)
    assert final_manifest == manifest
    guard.add_output(root)
    assert guard.finish(root/'r1_validation_run.json').value == 'VALID'
    _write_atomic(root/'r1_reconciliation.json',json.dumps(dict(source_commit=output_manifest['source_commit'],
        baseline_manifest=manifest,checks='PASS',canonical_read=False,provider_read=False,
        policy_migration=audit['input_policy_migration'],historical_failures_not_retried=audit['historical_failures_not_retried'],
        views='JSON/Chinese/CSV/AI reproduced from the same typed results',results=report),ensure_ascii=False,indent=2))
    print(json.dumps([{k:r[k] for k in ('symbol','candidate_count','formed_count','retrospective_held_count',
        'first_live_touch_count','confirmation_count','state','eligible')} for r in report],ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
