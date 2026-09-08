"""R1–R5 regressions using the real XNYS calendar and deterministic TEST prices."""
import pytest
from test_breakout_retest import sample, direct
from pcs.trend.opportunity_engine import evaluate_entry_opportunity


def test_r1_late_first_touch_cannot_confirm():
    result = direct(sample(44, retest=41, confirm=43))
    assert result.events[0].retest_session is None
    assert result.events[0].confirmation_session is None
    assert result.eligible_at_requested_time is False


@pytest.mark.parametrize('invalid', [False, True])
def test_r2_actual_gap_checkpoint(invalid):
    inp = sample(32, changes={28: {'close': None}}) if invalid else sample(32, missing=28)
    partial = direct(inp)
    assert partial.evaluated_through == inp.feature_view.expected_sessions[27]
    assert partial.next_state.state_revision == 3
    assert direct(inp, partial.next_state).result_id == partial.result_id
    restored = direct(sample(32), partial.next_state)
    cold = direct(sample(32))
    assert restored.result_id == cold.result_id
    assert restored.next_state == cold.next_state
    assert not restored.coverage_missing_evidence


def test_r2_sliding_display_start():
    inp = sample(32)
    moved = inp.model_copy(update={'feature_view': inp.feature_view.model_copy(
        update={'analysis_start': inp.feature_view.expected_sessions[30]})})
    assert direct(moved, direct(sample(25)).next_state).result_id == direct(inp).result_id


@pytest.mark.parametrize('field', ['volume', 'atr14', 'structure_state'])
def test_r3_unknown_discovery(field):
    result = direct(sample(25, changes={25: {field: None}}))
    assert result.eligible_at_requested_time is None
    assert result.status == 'PARTIAL'


@pytest.mark.parametrize('field', ['structure_state', 'trend_health', 'short_term_phase', 'atr14'])
def test_r3_unknown_current(field):
    result = evaluate_entry_opportunity(sample(30, changes={30: {field: None}}))
    assert result.eligible_at_requested_time is None
    assert result.status == 'PARTIAL'
    assert result.current_missing_details


def test_r3_false_dominates_unknown_and_diagnostics_do_not_block():
    assert direct(sample(25, changes={25: {'volume': None, 'close': 99.}})).eligible_at_requested_time is False
    assert direct(sample(25, changes={25: {'trend_health': None, 'short_term_phase': None}})).status == 'COMPLETED'


@pytest.mark.parametrize('through', [25, 27, 29])
def test_r4_prefix_snapshots(through):
    prefix = evaluate_entry_opportunity(sample(through))
    full = evaluate_entry_opportunity(sample(32))
    assert prefix.timeline == full.timeline[:len(prefix.timeline)]
    assert prefix.transitions == full.transitions[:len(prefix.transitions)]


def current_input(stamp):
    inp = sample(30)
    return inp.model_copy(update={'call_context': inp.call_context.model_copy(
        update={'mode': 'CURRENT_EOD', 'requested_as_of': stamp})})


def test_r5_expired_request_and_public_parity():
    inp = current_input('2026-03-02T16:30:00-05:00')
    pure, adapted = direct(inp), evaluate_entry_opportunity(inp)
    assert pure.eligible_at_requested_time is False
    assert adapted.eligible_at_requested_time is False
    assert adapted.requested_session == '2026-03-02'


def test_r5_timezone_required():
    with pytest.raises(ValueError, match='TIMEZONE_AWARE'):
        direct(current_input('2026-02-20'))


def test_r5_newer_request_inside_window_is_unknown():
    inp = sample(30)
    request_day = inp.feature_view.expected_sessions[31]
    inp = inp.model_copy(update={'call_context': inp.call_context.model_copy(
        update={'mode': 'CURRENT_EOD', 'requested_as_of': request_day+'T16:30:00-05:00'})})
    assert direct(inp).eligible_at_requested_time is None
    assert evaluate_entry_opportunity(inp).current_missing_details


def test_r2_consecutive_gaps_and_no_committed_session():
    inp = sample(32)
    days = inp.feature_view.expected_sessions
    inp = inp.model_copy(update={'feature_view': inp.feature_view.model_copy(update={
        'bars': [b for b in inp.feature_view.bars if b.session.isoformat() not in days[28:30]]})})
    partial = direct(inp)
    assert partial.missing_sessions == days[28:30]
    assert {g.session for g in partial.current_missing_details} == set(days[28:30])
    assert direct(sample(32), partial.next_state).next_state == direct(sample(32)).next_state
    empty = direct(sample(25, missing=25))
    assert empty.evaluated_through is None and empty.next_state.state_revision == 0
    assert direct(sample(25), empty.next_state).result_id == direct(sample(25)).result_id
    assert evaluate_entry_opportunity(sample(25, missing=25)).status == 'PARTIAL'


@pytest.mark.parametrize('field', ['volume', 'atr14', 'structure_state', 'trend_health', 'short_term_phase'])
def test_r3_confirmation_unknown_does_not_create_confirmation(field):
    result = direct(sample(29, changes={29: {field: None}}))
    assert result.events[0].confirmation_session is None
    assert result.eligible_at_requested_time is None
    assert result.current_missing_details
    assert all(g.evidence_refs for g in result.current_missing_details)


def test_r3_current_false_retains_other_unknowns_and_aggregate_unknown():
    # Use a guaranteed false distance gate, independent of phase vocabulary.
    result = evaluate_entry_opportunity(sample(30, changes={30: {'trend_health': None, 'atr14': 0.1}}))
    assert result.eligible_at_requested_time is False and result.current_missing_details
    inp = sample(25, changes={25: {'volume': None}}, families=('HEALTHY_PULLBACK', 'SHALLOW_PULLBACK', 'BREAKOUT_RETEST'))
    aggregate = evaluate_entry_opportunity(inp)
    assert aggregate.eligible_at_requested_time is None and aggregate.status == 'PARTIAL'


def test_r3_historical_unknown_does_not_block_current_false():
    result = direct(sample(26, changes={25: {'volume': None}, 26: {'close': 99.}}))
    assert result.eligible_at_requested_time is False
    assert result.coverage_missing_evidence
    assert all('current_assessment' not in g.affected_outputs
               for g in result.coverage_missing_evidence if g.session == result.timeline[0].session)


@pytest.mark.parametrize('stamp,session', [
    ('2026-02-17T16:30:00-05:00', '2026-02-17'),
    ('2026-02-18T10:30:00-05:00', '2026-02-17'),
    ('2026-02-21T12:00:00-05:00', '2026-02-20'),
    ('2026-02-16T17:00:00-05:00', '2026-02-13'),
])
def test_r5_real_xnys_resolution(stamp, session):
    inp = current_input(stamp)
    raw = direct(inp)
    adapted = evaluate_entry_opportunity(inp)
    assert raw.requested_session == adapted.requested_session == session
    assert raw.eligible_at_requested_time == adapted.eligible_at_requested_time
    assert direct(inp, raw.next_state).result_id == raw.result_id
    assert raw.events == direct(sample(30)).events


def test_r5_semantic_identity_ignores_receipt_but_includes_mode():
    inp = current_input('2026-02-17T16:30:00-05:00')
    other = current_input('2026-02-17T17:30:00-05:00')
    assert direct(inp).result_id == direct(other).result_id
    assert direct(inp).result_id != direct(sample(30)).result_id
    with pytest.raises(ValueError, match='MODE_UNSUPPORTED'):
        direct(inp.model_copy(update={'call_context': inp.call_context.model_copy(update={'mode': 'INVALID'})}))


def test_r4_all_views_use_prefix_fields():
    import csv
    import io
    from pcs.pool.opportunities import _csv_view, opportunity_to_ai_view, opportunities_to_markdown
    prefix = evaluate_entry_opportunity(sample(25))
    full = evaluate_entry_opportunity(sample(32))
    row = next(csv.DictReader(io.StringIO(_csv_view([full]))))
    assert row['confirmation_deadline'] == row['entry_start'] == row['economic_episode_id'] == ''
    assert opportunity_to_ai_view(full)['breakout_retest']['timeline'][0] == opportunity_to_ai_view(prefix)['breakout_retest']['timeline'][0]
    assert '未知' in opportunities_to_markdown([evaluate_entry_opportunity(sample(30, changes={30: {'trend_health': None}}))])


def test_old_calculation_artifacts_rejected():
    from pcs.trend.selection_models import BreakoutRetestResult, BreakoutRetestState
    result = direct(sample(30))
    old = result.model_dump(mode='json')
    old['calculation_version'] = 'breakout-retest-v1'
    with pytest.raises(ValueError):
        BreakoutRetestResult.model_validate(old)
    state = result.next_state.model_dump(mode='json')
    state['calculation_version'] = 'breakout-retest-v1'
    with pytest.raises(ValueError):
        BreakoutRetestState.model_validate(state)


def test_r1_expiry_keeps_explicit_invalidation_priority():
    result = direct(sample(41, retest=41, confirm=43, changes={41: {'close': 98., 'low': 97.}}))
    assert result.events[0].state == 'INVALIDATED'
    assert result.events[0].retest_session is None


def test_r3_waiting_structure_unknown_is_not_pass():
    result = direct(sample(26, changes={26: {'structure_state': None}}))
    assert result.eligible_at_requested_time is None
    assert next(c for c in result.timeline[-1].conditions
                if c.condition_id == 'BREAKOUT_STRUCTURE_NOT_BEARISH').predicate_value is None


def test_r5_saved_current_state_and_aggregate_restore(tmp_path):
    import json
    from pcs.trend.selection_models import EntryOpportunity, BreakoutRetestState
    inp = current_input('2026-02-18T16:30:00-05:00')
    raw = direct(inp)
    path = tmp_path/'checkpoint.json'
    path.write_text(raw.next_state.model_dump_json(), encoding='utf-8')
    restored = direct(inp, BreakoutRetestState.model_validate_json(path.read_text(encoding='utf-8')))
    assert raw.result_id == restored.result_id
    aggregate_input = inp.model_copy(update={
        'enabled_families': ['HEALTHY_PULLBACK', 'SHALLOW_PULLBACK', 'BREAKOUT_RETEST'],
        'support_result_ids': {b.session.isoformat(): 'TEST:empty-support-snapshot:'+b.session.isoformat()
                               for b in inp.feature_view.bars}})
    aggregate = evaluate_entry_opportunity(aggregate_input)
    path.write_text(aggregate.model_dump_json(), encoding='utf-8')
    saved = EntryOpportunity.model_validate(json.loads(path.read_text(encoding='utf-8')))
    again = evaluate_entry_opportunity(aggregate_input.model_copy(update={'prior_family_results': saved.family_results}))
    assert again.result_id == aggregate.result_id
    child = next(r for r in again.family_results if r.family == 'BREAKOUT_RETEST')
    assert child.eligible_at_requested_time is None
    assert child.breakout_result.result_id == raw.result_id
