from datetime import date

import pandas as pd
import pytest

from pcs.analysis_contracts import CallContext, SourceReference
from pcs.trend.selection_models import (
    ConfirmedSwingEvidence, SupportFeatureBar, SupportFeatureView, SupportZoneInput,
)
from pcs.trend.support_zones import evaluate_support_zones
from pcs.pool.support_zones import (
    find_support_test, find_support_zone, load_support_zone_state,
    support_zone_to_ai_view, support_zones_to_markdown, write_support_zone_artifacts,
)


def support_input(rows, *, swings=(), asof=None, prior=None, bound=None,
                  run="run", request="request", received="2026-01-01T00:00:00Z",
                  source_record="fixture-v1", price_basis="TEST_ADJUSTED"):
    sessions = [str(d.date()) for d in pd.bdate_range("2025-01-06", periods=len(rows)+3)]
    bars = []
    for session, row in zip(sessions, rows):
        values = {"open": 101, "high": 102, "low": 101, "close": 101,
                  "sma20": 100, "sma50": 100.2, "atr14": 2}
        values.update(row)
        values["high"] = max(values["high"], values["open"], values["close"])
        values["low"] = min(values["low"], values["open"], values["close"])
        bars.append(SupportFeatureBar(session=session, **values))
    effective = asof or bars[-1].session.isoformat()
    view = SupportFeatureView(symbol="TEST", bars=bars, confirmed_swings=list(swings),
        expected_sessions=sessions, analysis_start=sessions[0], indicator_seed_start="2024-01-02",
        indicator_identity="production-talib:atr14:sma20:sma50:pivot3x3", price_basis=price_basis,
        corporate_action_version="TEST_CA", input_kind="TEST", received_at=received,
        source=SourceReference(source_id="TEST:daily", source_kind="TEST", record_identity=source_record))
    ctx = CallContext(symbol="TEST", requested_as_of=effective, effective_daily_session=effective,
        mode="HISTORICAL", run_id=run, request_id=request, scope="SUPPORT_ZONES")
    return SupportZoneInput(call_context=ctx, feature_view=view, prior_state=prior, bound_zone_id=bound)


def events(result, kind):
    return [h for h in result.support_history if h.event_type == kind]


def first_zone(result):
    return (result.current_zones + result.archived_zones)[0]


def test_unconfirmed_pivot_unavailable_and_confirmed_at_controls_known_time():
    rows = [{} for _ in range(5)]
    rows[1] = {"low": 94, "high": 96, "close": 95}
    future = ConfirmedSwingEvidence(source_id="pivot:S1", pivot_date="2025-01-07",
        confirmed_at="2025-01-13", swing_type="low", price=95)
    early = evaluate_support_zones(support_input(rows, swings=[future]))
    assert all(a.source_id != "pivot:S1" for z in early.current_zones for a in z.creation_sources)
    full = evaluate_support_zones(support_input(rows+[{}], swings=[future]))
    pivot_zone = next(z for z in full.current_zones if any(a.source_id == "pivot:S1" for a in z.creation_sources))
    assert pivot_zone.formed_at == "2025-01-07"
    assert pivot_zone.available_at == "2025-01-13"
    retro = next(h for h in full.support_history if h.zone_id == pivot_zone.zone_id and h.event_type == "RETROSPECTIVE_INTERSECTION")
    assert retro.retrospective and retro.known_at == "2025-01-13"
    assert not pivot_zone.tests


def test_single_source_is_retained_and_adjacent_mas_are_one_zone_not_tests():
    one = evaluate_support_zones(support_input([{"sma50": None}, {}]))
    assert any(len(z.creation_sources) == 1 for z in one.current_zones)
    clustered = evaluate_support_zones(support_input([{}, {}]))
    zone = first_zone(clustered)
    assert {a.source_type for a in zone.creation_sources} == {"SMA20", "SMA50"}
    assert len(zone.tests) == 0


def test_cluster_is_bounded_not_chain_merged():
    swing = ConfirmedSwingEvidence(source_id="pivot", pivot_date="2025-01-06",
        confirmed_at="2025-01-09", swing_type="low", price=100.8)
    no_ma = {"sma20": None, "sma50": None}
    r = evaluate_support_zones(support_input([no_ma, no_ma, no_ma,
        {"sma20": 100, "sma50": 100.3}], swings=[swing]))
    assert len(r.current_zones) == 2
    assert all(z.upper-z.lower == pytest.approx(.7) for z in r.current_zones)


def test_touch_day_never_confirms_and_continuous_touch_is_one_test():
    rows = [{}, {"low": 99.8, "high": 101, "close": 101.5},
            {"low": 99.5, "high": 101.5, "close": 100.1},
            {"low": 99.7, "high": 102, "close": 101.4}]
    touch = evaluate_support_zones(support_input(rows, asof="2025-01-07"))
    assert first_zone(touch).state == "TEST_IN_PROGRESS"
    final = evaluate_support_zones(support_input(rows))
    zone = first_zone(final)
    assert len(zone.tests) == 1 and zone.tests[0].status == "HELD"
    assert zone.tests[0].first_held_at == "2025-01-09"
    assert zone.tests[0].cumulative_low == 99.5


def test_confirmation_day_three_allowed_day_four_is_unconfirmed_not_broken():
    prefix = [{}, {"low": 99.8, "high": 101, "close": 100},
              {"low": 99.8, "high": 101, "close": 100},
              {"low": 99.8, "high": 101, "close": 100}]
    day3 = evaluate_support_zones(support_input(prefix+[{
        "low": 99.5, "high": 102, "close": 101.4}]))
    assert first_zone(day3).tests[0].status == "HELD"
    day4 = evaluate_support_zones(support_input(prefix+[{
        "low": 99.8, "high": 101, "close": 100},
        {"low": 99.5, "high": 102, "close": 101.4}]))
    zone = first_zone(day4)
    assert zone.tests[0].status == "UNCONFIRMED_TEST"
    assert zone.state == "REFERENCE_ONLY" and zone.broken_at is None


def test_departure_then_later_retouch_creates_second_test():
    rows = [{}, {"low": 99.5, "high": 101, "close": 100},
            {"low": 99.5, "high": 102, "close": 101.4},
            {"low": 101, "high": 103, "close": 101.5},
            {"low": 99.8, "high": 101, "close": 100},
            {"low": 99.5, "high": 102, "close": 101.4}]
    r = evaluate_support_zones(support_input(rows))
    zone = first_zone(r)
    assert len(zone.tests) == 2
    assert zone.tests[0].departure_session == "2025-01-09"
    assert zone.tests[1].touch_session == "2025-01-10"
    assert zone.state == "REPEATED_HELD_TESTS"
    assert all(t.rebound_atr is not None and t.penetration_atr is not None for t in zone.tests)


def test_break_line_frozen_and_past_held_preserved():
    rows = [{}, {"low": 99.5, "high": 101, "close": 100},
            {"low": 99.5, "high": 102, "close": 101.4},
            {"low": 98, "high": 102, "close": 98.9, "atr14": 20, "sma20": 80, "sma50": 70}]
    r = evaluate_support_zones(support_input(rows))
    zone = next(z for z in r.archived_zones if z.broken_at)
    assert zone.state == "BROKEN" and zone.invalidation_line == pytest.approx(99.05)
    assert zone.tests[0].status == "HELD"
    assert zone.broken_at == "2025-01-09"
    assert events(r, "INTRADAY_PENETRATION") and events(r, "BROKEN")


def test_intraday_penetration_is_not_close_break():
    r = evaluate_support_zones(support_input([{}, {"low": 98, "high": 102, "close": 100}]))
    zone = first_zone(r)
    assert zone.state == "TEST_IN_PROGRESS" and zone.broken_at is None
    assert len(zone.intraday_breaches) == 1


def test_future_data_does_not_change_historical_result():
    rows = [{}, {"low": 99.5, "high": 101, "close": 100},
            {"low": 99.5, "high": 102, "close": 101.4}]
    early = evaluate_support_zones(support_input(rows[:2], asof="2025-01-07"))
    with_future = evaluate_support_zones(support_input(rows, asof="2025-01-07"))
    assert early == with_future


def test_batch_replay_incremental_resume_and_repeat_are_equivalent():
    rows = [{}, {"low": 99.5, "high": 101, "close": 100},
            {"low": 99.5, "high": 102, "close": 101.4},
            {"low": 101, "high": 103, "close": 101.5},
            {"low": 99.8, "high": 101, "close": 100},
            {"low": 99.5, "high": 102, "close": 101.4}]
    batch = evaluate_support_zones(support_input(rows))
    first = evaluate_support_zones(support_input(rows, asof="2025-01-08"))
    resumed = evaluate_support_zones(support_input(rows, prior=first.next_state,
        run="other", request="other", received="2027-01-01T00:00:00Z"))
    repeat = evaluate_support_zones(support_input(rows, prior=resumed.next_state,
        run="third", request="third", received="2028-01-01T00:00:00Z"))
    assert batch.current_zones == resumed.current_zones == repeat.current_zones
    assert batch.archived_zones == resumed.archived_zones == repeat.archived_zones
    assert batch.support_history == resumed.support_history == repeat.support_history
    assert batch.next_state == resumed.next_state == repeat.next_state
    assert batch.result_id == resumed.result_id == repeat.result_id
    assert not repeat.state_changes


def test_changed_prefix_or_price_identity_replays_instead_of_continuing():
    rows = [{}, {}, {}]
    first = evaluate_support_zones(support_input(rows, asof="2025-01-07"))
    changed = rows.copy(); changed[1] = {"close": 99.9}
    replay = evaluate_support_zones(support_input(changed, prior=first.next_state))
    assert "PRIOR_STATE_INVALIDATED_REPLAYED" in replay.call_diagnostics
    cold = evaluate_support_zones(support_input(changed))
    assert replay.status.value == cold.status.value == "COMPLETED"
    assert replay.result_id == cold.result_id
    assert replay.next_state == cold.next_state
    assert replay.current_zones == cold.current_zones
    assert replay.archived_zones == cold.archived_zones
    assert replay.support_history == cold.support_history
    rebased = evaluate_support_zones(support_input(rows, prior=first.next_state,
        price_basis="OTHER_ADJUSTED"))
    assert "PRIOR_STATE_INVALIDATED_REPLAYED" in rebased.call_diagnostics


def test_missing_intermediate_session_stops_at_last_known_state():
    rows = [{}, {}, {}]
    inp = support_input(rows)
    missing = inp.feature_view.model_copy(update={"bars": [inp.feature_view.bars[0], inp.feature_view.bars[2]]})
    r = evaluate_support_zones(inp.model_copy(update={"feature_view": missing}))
    assert r.coverage.evaluated_through == "2025-01-06"
    assert r.status.value == "PARTIAL"
    assert "INTERMEDIATE_DAILY_SESSION_MISSING" in r.reason_codes
    assert not any(h.session == "2025-01-08" for h in r.support_history)


def test_binding_roles_and_unselected_zones_are_explicit():
    r = evaluate_support_zones(support_input([{}]))
    assert next(s for s in r.selections if s.role == "BOUND_SUPPORT").status == "UNBOUND"
    key = next(s.zone_id for s in r.selections if s.role == "CANDIDATE_KEY_SUPPORT")
    bound = evaluate_support_zones(support_input([{}, {}], bound=key))
    assert next(s for s in bound.selections if s.role == "BOUND_SUPPORT").zone_id == key
    assert any(z.zone_id == key and z.bound for z in bound.current_zones)


def test_moving_ma_archives_old_unbound_zone_but_never_bound_zone():
    rows = [{"sma20": 100, "sma50": None},
            {"sma20": 110, "sma50": None, "low": 109, "high": 111, "close": 111}]
    first = evaluate_support_zones(support_input(rows, asof="2025-01-06"))
    old_id = first.current_zones[0].zone_id
    moved = evaluate_support_zones(support_input(rows))
    old = next(z for z in moved.archived_zones if z.zone_id == old_id)
    assert old.archive_reason == "MOVING_MA_REFERENCE_REPLACED"
    assert all(z.zone_id != old_id for z in moved.current_zones)
    bound = evaluate_support_zones(support_input(rows, bound=old_id))
    assert any(z.zone_id == old_id and z.active and z.bound for z in bound.current_zones)


def test_run_request_and_receipt_do_not_change_business_identity():
    a = evaluate_support_zones(support_input([{}, {}]))
    b = evaluate_support_zones(support_input([{}, {}], run="b", request="b", received="2030-01-01T00:00:00Z"))
    assert a.received_at != b.received_at
    assert a.current_zones == b.current_zones and a.support_history == b.support_history
    assert a.result_id == b.result_id


def test_artifacts_roundtrip_queries_hashes_and_saved_state(tmp_path):
    result = evaluate_support_zones(support_input([{}, {"low": 99.5, "high": 101, "close": 100},
        {"low": 99.5, "high": 102, "close": 101.4}]))
    root = write_support_zone_artifacts(tmp_path/"out", [result], audit={"kind": "TEST"})
    stored = __import__("json").loads((root/"support_zone_results.json").read_text(encoding="utf-8"))[0]
    from pcs.trend.selection_models import SupportZoneResult
    assert SupportZoneResult.model_validate(stored) == result
    assert load_support_zone_state(root, "TEST") == result.next_state
    zone = first_zone(result)
    assert find_support_zone(result, zone.zone_id) == zone
    assert find_support_test(result, zone.tests[0].test_id) == zone.tests[0]
    assert "建区ATR" in support_zones_to_markdown([result])
    assert support_zone_to_ai_view(result)["result_id"] == result.result_id
    manifest = __import__("json").loads((root/"artifact_manifest.json").read_text())
    from hashlib import sha256
    assert all(sha256((root/name).read_bytes()).hexdigest() == digest
               for name, digest in manifest["sha256"].items())
    with pytest.raises(ValueError, match="NOT_EMPTY"):
        write_support_zone_artifacts(root, [result])


@pytest.mark.parametrize("parameter,value", [("zone_width_atr", .70), ("break_buffer_atr", .90)])
def test_zone_identity_binds_effective_definition(parameter, value):
    from pcs.trend.selection_models import SupportZonePolicy
    inp = support_input([{}, {"low": 99.8}])
    original = evaluate_support_zones(inp)
    changed = evaluate_support_zones(inp.model_copy(update={
        "effective_policy": SupportZonePolicy(**{parameter: value})}))
    assert first_zone(original).zone_id != first_zone(changed).zone_id
    assert first_zone(original).policy_sha256 != first_zone(changed).policy_sha256
    assert first_zone(original).tests[0].test_id != first_zone(changed).tests[0].test_id
    assert {h.zone_id for h in changed.support_history} == {first_zone(changed).zone_id}
    assert changed.next_state.zones == changed.current_zones + changed.archived_zones
    assert evaluate_support_zones(inp).result_id == original.result_id


def test_intraday_only_breach_survives_all_views_and_saved_resume(tmp_path):
    rows = [{}, {"open": 99.3, "high": 99.5, "low": 98.9, "close": 99.3}]
    result = evaluate_support_zones(support_input(rows))
    zone = first_zone(result)
    assert zone.state == "REFERENCE_ONLY" and not zone.tests and zone.broken_at is None
    assert len(zone.intraday_breaches) == 1
    breach = zone.intraday_breaches[0]
    history = events(result, "INTRADAY_PENETRATION")
    assert len(history) == 1
    assert (history[0].session, history[0].low, history[0].invalidation_line) == (
        breach.session, breach.low, breach.invalidation_line)
    root = write_support_zone_artifacts(tmp_path/"breach", [result])
    state = load_support_zone_state(root, "TEST")
    repeat = evaluate_support_zones(support_input(rows, prior=state))
    assert repeat.result_id == result.result_id
    assert first_zone(repeat).intraday_breaches == [breach]
    extended = rows + [{"open": 99.3, "high": 99.5, "low": 99.2, "close": 99.3}]
    resumed = evaluate_support_zones(support_input(extended, prior=state))
    assert resumed.next_state == evaluate_support_zones(support_input(extended)).next_state
    assert first_zone(resumed).intraday_breaches == [breach]


def test_later_sources_have_queryable_values_and_causal_links(tmp_path):
    import json
    from pcs.pool.support_zones import find_support_source, support_source_details
    swing = ConfirmedSwingEvidence(source_id="later-pivot", pivot_date="2025-01-06",
        confirmed_at="2025-01-09", swing_type="low", price=100.1)
    rows = [{}, {}, {}, {"sma20": 100.3}]
    result = evaluate_support_zones(support_input(rows, swings=[swing]))
    zone = first_zone(result)
    assert len(zone.creation_sources) == 2 and not zone.tests
    assert (zone.lower, zone.upper) == pytest.approx((99.75, 100.45))
    assert len(zone.observed_source_ids) == len(set(zone.observed_source_ids)) == len(zone.observed_sources)
    anchor = find_support_source(result, zone.zone_id, "later-pivot")
    assert (anchor.price, anchor.observed_at, anchor.available_at, anchor.pivot_date) == (
        100.1, "2025-01-06", "2025-01-09", "2025-01-06")
    assert anchor not in zone.creation_sources
    ma = find_support_source(result, zone.zone_id, "SMA20:2025-01-09")
    assert ma.price == 100.3 and ma.available_at == "2025-01-09"
    links = [h for h in result.support_history if "later-pivot" in h.source_ids]
    assert len(links) == 1 and links[0].known_at == "2025-01-09"
    assert links[0].event_type == "SOURCE_RESONANCE"
    root = write_support_zone_artifacts(tmp_path/"sources", [result])
    detail = json.loads((root/"support_sources.json").read_text(encoding="utf-8"))
    assert detail == support_source_details(result) == support_zone_to_ai_view(result)["sources"]
    assert any(a["source_id"] == "later-pivot" and a["role"] == "LATER_OBSERVATION" and a["price"] == 100.1 for a in detail)
    assert "100.3 | 2025-01-09 | 2025-01-09" in (root/"support_zones.zh-CN.md").read_text(encoding="utf-8")
    state = load_support_zone_state(root, "TEST")
    repeated = evaluate_support_zones(support_input(rows, swings=[swing], prior=state))
    assert repeated.next_state == result.next_state and repeated.result_id == result.result_id


def test_legacy_state_replays_and_missing_replay_stays_partial():
    inp = support_input([{}, {}, {}])
    full = evaluate_support_zones(inp)
    old = full.next_state.model_copy(update={"policy_sha256": "legacy-v1-policy"})
    replay = evaluate_support_zones(inp.model_copy(update={"prior_state": old}))
    assert replay.status.value == "COMPLETED" and replay.result_id == full.result_id
    assert replay.call_diagnostics == ["PRIOR_STATE_INVALIDATED_REPLAYED"]
    missing = inp.feature_view.model_copy(update={"bars": inp.feature_view.bars[::2]})
    partial = evaluate_support_zones(inp.model_copy(update={"feature_view": missing, "prior_state": old}))
    assert partial.status.value == "PARTIAL" and partial.coverage.evaluated_through == "2025-01-06"
    assert partial.call_diagnostics and "INTERMEDIATE_DAILY_SESSION_MISSING" in partial.reason_codes
