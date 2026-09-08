"""Reconcile hash-verified saved Step 6 results against the corrected export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pcs.pool.artifacts import _write_atomic
from pcs.pool.opportunities import read_opportunity_bundle
from pcs.trend.selection_models import EntryOpportunity
from pcs.validation import ValidationRun


def differences(before, after, path=''):
    if isinstance(before, dict) and isinstance(after, dict):
        return [change for key in sorted(before.keys() | after.keys())
                for change in differences(before.get(key), after.get(key), f'{path}/{key}')]
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [change for i, (a, b) in enumerate(zip(before, after))
                for change in differences(a, b, f'{path}/{i}')]
    return [] if before == after else [{'path': path, 'before': before, 'after': after}]


def explanation(change):
    path = change['path']
    if any(token in path for token in ('_id', 'source_ids', 'source_refs', 'source_id', 'observed_sources', 'creation_sources')):
        return 'Calculation/policy version changes evidence identities; economic identity stays tied to symbol and legal retest date. R4 removes identities not yet known on past dates.'
    if any(token in path for token in ('entry_start', 'entry_end', 'confirmation_deadline', 'economic_episode')):
        return 'R4: daily fields now contain only facts known on that session.'
    if any(token in path for token in ('eligible', 'status', 'missing', 'conditions')):
        return 'R3/R5: required unknowns and request-time applicability are represented separately from conclusive false.'
    if any(token in path for token in ('retest_session', 'confirmation_session', 'state', 'terminal_session')):
        return 'R1/R3: first-touch deadline and required evidence govern lifecycle advancement.'
    return 'R2/R4: committed boundary and explicit causal snapshot fields; see before/after values.'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('old_directory')
    parser.add_argument('new_directory')
    args = parser.parse_args()
    root = Path(args.new_directory)
    code = Path(__file__).resolve().parents[1]
    guard = ValidationRun(code, tuple((code/'src/pcs/trend').glob('*.py')) +
        (Path(__file__), root/'artifact_manifest.json', Path(args.old_directory)/'artifact_manifest.json'))
    old_manifest, old = read_opportunity_bundle(args.old_directory)
    new_manifest, new = read_opportunity_bundle(root)
    assert new_manifest['tracked_source_dirty'] is False
    old_results = {r['symbol']: r for r in old['entry_opportunities.json']}
    new_results = {r['symbol']: EntryOpportunity.model_validate(r).model_dump(mode='json')
                   for r in new['entry_opportunities.json']}
    assert len(new_results) == 7 and old_results.keys() == new_results.keys()
    old_inputs = {r['call_context']['symbol']: r for r in old['prepared_opportunity_inputs.json']}
    new_inputs = {r['call_context']['symbol']: r for r in new['prepared_opportunity_inputs.json']}
    rows = []
    for symbol, result in new_results.items():
        previous = old_results[symbol]
        assert old_inputs[symbol]['feature_view'] == new_inputs[symbol]['feature_view']
        old_children = {r['family']: r for r in previous['family_results']}
        new_children = {r['family']: r for r in result['family_results']}
        for family in ('HEALTHY_PULLBACK', 'SHALLOW_PULLBACK'):
            assert old_children[family]['result_id'] == new_children[family]['result_id']
        a, b = old_children['BREAKOUT_RETEST'], new_children['BREAKOUT_RETEST']
        changes = differences(a['breakout_result']['events'], b['breakout_result']['events'], '/events')
        day_changes = differences(a['timeline'], b['timeline'], '/timeline')
        for change in changes + day_changes:
            change['explanation'] = explanation(change)
        rows.append({'symbol': symbol, 'saved_feature_view_unchanged': True,
            'old_family_child_ids_unchanged': True,
            'old_breakout_result_id': a['result_id'], 'new_breakout_result_id': b['result_id'],
            'id_change_reason': 'breakout-retest-v2 / entry-opportunity-v2.4; policy identity and causal timeline changed',
            'state_before': a['state'], 'state_after': b['state'],
            'eligible_before': a['eligible_at_requested_time'], 'eligible_after': b['eligible_at_requested_time'],
            'counts_before': counts(a), 'counts_after': counts(b),
            'event_changes': changes, 'daily_changes': day_changes})
    report = {'source_commit': new_manifest['source_commit'], 'old_source_commit': old_manifest['source_commit'],
        'old_manifest': old_manifest, 'new_manifest': new_manifest,
        'canonical_read': False, 'provider_read': False,
        'checks': 'PASS: both bundle hashes, seven unchanged saved feature views, fourteen old family child IDs',
        'results': rows, 'failures': new['read_audit.json']['failures']}
    read_opportunity_bundle(args.old_directory)
    read_opportunity_bundle(root)
    guard.add_output(root)
    assert guard.finish(root/'reconciliation_validation_run.json').value == 'VALID'
    _write_atomic(root/'r1_r5_reconciliation.json', json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps([{k: v for k, v in row.items() if k not in {'event_changes', 'daily_changes'}}
                      for row in rows], ensure_ascii=False, indent=2))


def counts(result):
    events = result['breakout_result']['events']
    return [len(events), sum(e['retest_session'] is not None for e in events),
            sum(e['confirmation_session'] is not None for e in events)]


if __name__ == '__main__':
    main()
