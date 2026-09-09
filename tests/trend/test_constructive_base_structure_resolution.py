"""R1: one authoritative structure value per platform session (TEST prices)."""
import pytest
from test_constructive_base import sample, direct
from pcs.trend.selection_models import BaseStructureEvidence, BaseState, BaseResult, ConstructiveBasePolicy, EntryOpportunity
from pcs.trend.opportunity_engine import evaluate_entry_opportunity


def with_detail(index, bar='bullish', detail='neutral', **fields):
    inp = sample(index, changes={index: {'structure_state': bar}})
    evidence = BaseStructureEvidence(session=inp.feature_view.expected_sessions[index],
        source=inp.feature_view.source.model_copy(update={'source_id': 'TEST:detail'}), structure_state=detail,
        calculation_version='TEST:structure', **fields)
    return inp.model_copy(update={'base_structure_evidence': [evidence]})


@pytest.mark.parametrize('index,cid', [(42,'STRUCTURE_BULLISH_CONFIRMATION'), (43,'STRUCTURE_STILL_BULLISH')])
def test_neutral_detail_cannot_pass_bullish_gate(index, cid):
    result = direct(with_detail(index))
    conditions = {c.condition_id: c for c in result.timeline[-1].conditions}
    assert conditions['BASE_STRUCTURE_NOT_BEARISH'].left_value == 'neutral'
    assert conditions[cid].left_value == 'neutral'
    assert conditions[cid].predicate_value is False
    assert result.eligible_at_requested_time is False
    assert (result.base_events[0].confirmation_session is None) == (index == 42)
    resolution = result.timeline[-1].structure_resolution
    assert resolution.selected_value == 'neutral' and resolution.bar_value == 'bullish'
    assert resolution.selected_from == 'DETAIL_STATE'
    assert resolution.overridden_sources == ['FEATURE_BAR']
    for cid in ('BASE_STRUCTURE_NOT_BEARISH', cid):
        assert 'TEST:detail' in conditions[cid].source_refs
        assert f'structure_resolution:{resolution.resolution_id}' in conditions[cid].source_refs
        assert 'BASE_STRUCTURE_SOURCE_CONFLICT_RESOLVED' in conditions[cid].reason_codes


@pytest.mark.parametrize('bar,detail,value,origin,overridden', [
    ('neutral','bullish','bullish','DETAIL_STATE',['FEATURE_BAR']),
    ('bearish','bullish','bullish','DETAIL_STATE',['FEATURE_BAR']),
    ('bullish','bullish','bullish','DETAIL_STATE',[]),
    ('bullish',None,'bullish','FEATURE_BAR',[]),
    (None,'bullish','bullish','DETAIL_STATE',[]),
    (None,None,None,'FEATURE_BAR',[]),
])
def test_authority_is_consistent_in_both_directions(bar, detail, value, origin, overridden):
    result = direct(with_detail(42, bar, detail))
    resolution = result.timeline[-1].structure_resolution
    assert (resolution.selected_value,resolution.selected_from,resolution.overridden_sources) == (value,origin,overridden)
    conditions = {c.condition_id:c for c in result.timeline[-1].conditions}
    assert conditions['STRUCTURE_BULLISH_CONFIRMATION'].left_value == value
    assert bool(result.base_events[0].confirmation_session) == (value == 'bullish')
    if value is None:
        assert conditions['STRUCTURE_BULLISH_CONFIRMATION'].predicate_value is None
        assert result.eligible_at_requested_time is None and result.status.value == 'PARTIAL'


@pytest.mark.parametrize('index', [39,42,43])
def test_absent_details_use_bar_without_new_requirement(index):
    result = direct(sample(index))
    resolution = result.timeline[-1].structure_resolution
    assert resolution.selected_from == 'FEATURE_BAR' and resolution.selected_value == 'bullish'
    assert resolution.detail is None and not resolution.overridden_sources
    assert 'TEST:prepared' in resolution.selected_source_refs
    assert result.base_detected is True


@pytest.mark.parametrize('fields', [{'detail':'bearish'}, {'detail':'bullish','high_comparison':'lower','low_comparison':'lower'}])
def test_bearish_invalidation_precedes_unknown_and_confirmation(fields):
    inp = with_detail(42, **fields)
    bar = inp.feature_view.bars[-1].model_copy(update={'trend_health':None})
    inp = inp.model_copy(update={'feature_view':inp.feature_view.model_copy(update={'bars':inp.feature_view.bars[:-1]+[bar]})})
    result = direct(inp)
    assert result.opportunity_state == 'INVALIDATED' and result.eligible_at_requested_time is False
    assert result.base_events[0].confirmation_session is None
    condition = next(c for c in result.base_events[0].terminal_conditions if c.condition_id == 'BASE_STRUCTURE_NOT_BEARISH')
    assert condition.left_value == 'bearish' and condition.predicate_value is False
    assert 'BASE_STRUCTURE_SOURCE_CONFLICT_RESOLVED' in condition.reason_codes
    if fields['detail'] == 'bullish':
        assert result.timeline[-1].structure_resolution.overridden_sources == ['FEATURE_BAR','DETAIL_STATE']


def test_neutral_formation_and_preceding_evidence_use_same_authority():
    result = direct(with_detail(39))
    assert result.base_detected is True
    candidate = result.formation_candidates[0]
    assert candidate.structure_resolutions[-1].selected_value == 'neutral'
    inp = sample(39)
    details = [BaseStructureEvidence(session=s,source=inp.feature_view.source,structure_state='neutral',
        calculation_version='TEST:structure') for s in inp.feature_view.expected_sessions[:20]]
    result = direct(inp.model_copy(update={'base_structure_evidence':details}))
    assert not result.base_events
    gate = next(c for c in result.formation_candidates[0].conditions if c.condition_id == 'BASE_PRECEDING_BULLISH_EXISTS')
    assert gate.left_value == 0 and gate.predicate_value is False
    assert 'BASE_STRUCTURE_SOURCE_CONFLICT_RESOLVED' in gate.reason_codes
    assert len([r for r in gate.source_refs if r.startswith('structure_resolution:')]) == 20


@pytest.mark.parametrize('index', [42,43])
def test_serialized_resume_cold_and_public_adapter_parity(index):
    inp = with_detail(index)
    raw_before = inp.model_dump_json()
    cold = direct(inp)
    cut = inp.feature_view.expected_sessions[index-1]
    prefix_input = inp.model_copy(update={'call_context':inp.call_context.model_copy(update={
        'effective_daily_session':cut,'requested_as_of':cut})})
    prefix = direct(prefix_input)
    restored = direct(inp, BaseState.model_validate_json(prefix.next_state.model_dump_json()))
    assert restored.result_id == cold.result_id and restored.next_state == cold.next_state
    assert prefix.timeline == cold.timeline[:-1]
    assert all(r.detail is None for c in prefix.formation_candidates for r in c.structure_resolutions)
    assert direct(inp, cold.next_state).result_id == cold.result_id
    adapter = evaluate_entry_opportunity(inp)
    assert adapter.base_result == cold
    saved_adapter = EntryOpportunity.model_validate_json(evaluate_entry_opportunity(prefix_input).model_dump_json())
    assert evaluate_entry_opportunity(inp.model_copy(update={'prior_family_results':[saved_adapter]})).result_id == adapter.result_id
    assert inp.model_dump_json() == raw_before
    with pytest.raises(ValueError, match='BASE_PRIOR_REPLAY_REQUIRED'):
        direct(inp, direct(sample(index)).next_state)


def test_old_semantic_versions_are_rejected():
    current = direct(sample(42))
    for cls, payload in [(BaseState,current.next_state.model_dump(mode='json')),
                         (BaseResult,current.model_dump(mode='json')),
                         (ConstructiveBasePolicy,current.effective_policy.model_dump(mode='json'))]:
        payload['calculation_version'] = 'constructive-base-v1'
        with pytest.raises(ValueError):
            cls.model_validate(payload)


def test_conflicting_detail_does_not_change_old_family_ids():
    families = ['HEALTHY_PULLBACK','SHALLOW_PULLBACK','BREAKOUT_RETEST']
    plain = evaluate_entry_opportunity(sample(43,families=families))
    inp = with_detail(43).model_copy(update={'enabled_families':families+['CONSTRUCTIVE_BASE']})
    combined = evaluate_entry_opportunity(inp)
    assert [r.result_id for r in combined.family_results[:3]] == [r.result_id for r in plain.family_results]
    assert combined.family_results[3].base_result == direct(inp)


def test_conflict_four_views_share_selected_value_and_source(tmp_path):
    import csv, json
    from pcs.pool.opportunities import write_opportunity_artifacts, read_opportunity_bundle, opportunity_to_ai_view
    inp = with_detail(43)
    result = evaluate_entry_opportunity(inp)
    write_opportunity_artifacts(tmp_path,[result],inputs=[inp])
    _, docs = read_opportunity_bundle(tmp_path)
    resolution = result.base_result.timeline[-1].structure_resolution.model_dump(mode='json')
    assert docs['entry_opportunities.json'][0]['base_result']['timeline'][-1]['structure_resolution'] == resolution
    ai = json.loads((tmp_path/'entry_opportunities.ai.json').read_text(encoding='utf-8'))
    assert ai == [opportunity_to_ai_view(result)]
    assert ai[0]['constructive_base']['timeline'][-1]['structure_resolution'] == resolution
    rows = list(csv.DictReader((tmp_path/'entry_opportunities.csv').read_text(encoding='utf-8').splitlines()))
    assert json.loads(rows[-1]['base_structure_resolution']) == resolution
    gates = json.loads(rows[-1]['base_structure_conditions'])
    assert next(c for c in gates if c['condition_id']=='STRUCTURE_STILL_BULLISH')['left_value']=='neutral'
    assert rows[-1]['eligible']=='false'
    chinese = (tmp_path/'entry_opportunities.zh-CN.md').read_text(encoding='utf-8')
    assert '结构采用neutral' in chinese and '原bar=bullish' in chinese
    assert 'BASE_STRUCTURE_SOURCE_CONFLICT_RESOLVED' in chinese and resolution['resolution_id'] in chinese
