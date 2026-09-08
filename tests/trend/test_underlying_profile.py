"""Hand-calculated causal invariants; fixtures never represent canonical data."""
import json

import pytest
from pydantic import ValidationError

from pcs.analysis_contracts import CallContext, SourceReference
from pcs.trend.selection_models import DailyBar, DailyFeatureView, ProfileInput, ProfilePolicy, UnderlyingProfile
from pcs.trend.underlying_profile import measure_underlying_profile, profile_sessions
from pcs.pool.underlying_profiles import underlying_profile_to_ai_view, underlying_profiles_to_markdown, write_underlying_profile_artifacts


def make_input(tail=(), *, size=326, volume=1000, benchmark=True):
    sessions = profile_sessions("2026-09-04", ProfilePolicy())
    prices = [100.] * (size-len(tail)) + list(tail)
    bars = [DailyBar(session=s, open=p, high=p+1, low=p-1, close=p, volume=volume)
            for s, p in zip(sessions[-size:], prices)]
    view = DailyFeatureView(symbol="TEST", bars=bars, input_kind="TEST", currency="USD",
        price_basis="canonical_adjusted", corporate_action_version="TEST", source=SourceReference(
            source_id="TEST:hand-sequence", source_kind="TEST", record_identity="fixture", validated=False))
    ctx = CallContext(symbol="TEST", requested_as_of="2026-09-04", effective_daily_session="2026-09-04",
                      mode="HISTORICAL", run_id="TEST", request_id="TEST", scope="UNDERLYING_PROFILE")
    return ProfileInput(call_context=ctx, feature_view=view,
        benchmark=view.model_copy(update={"symbol": "BENCH"}) if benchmark else None)


def metric(result, name):
    return next(m for m in result.measurements if m.metric_id == name)


def test_complete_recovery_freezes_peak_and_one_episode():
    r = measure_underlying_profile(make_input([95, 90, 96, 100]))
    assert len(r.episodes) == 1
    e = r.episodes[0]
    assert e.frozen_peak_close == 100 and e.trough_close == 90
    assert e.max_depth == pytest.approx(.1)
    assert e.recovered and e.recovery_sessions == 3
    assert not e.left_truncated and not e.right_censored
    assert metric(r, "recovered_conditional_median").value == 3
    assert metric(r, "recovery_km_median").value == 3


def test_rolling_peak_decrease_never_means_recovery():
    r = measure_underlying_profile(make_input([94]*70 + [96]))
    assert len(r.episodes) == 1
    e = r.episodes[0]
    assert e.frozen_peak_close == 100 and not e.recovered
    assert e.right_censored and e.observed_sessions == 70
    assert metric(r, "current_rolling_drawdown").value == 0
    assert metric(r, "recovered_conditional_median").value is None
    assert metric(r, "recovery_km_median").value is None


def test_multiple_drawdown_days_not_multiple_episodes():
    r = measure_underlying_profile(make_input([95, 94, 93, 92, 91, 90]))
    assert len(r.episodes) == 1
    assert r.episodes[0].observed_sessions == 5
    assert metric(r, "unrecovered_count").value == 1


def test_left_truncation_visible_excluded_from_recovery():
    # Trigger precedes the 252-session observation boundary (index 74).
    r = measure_underlying_profile(make_input([95]*270+[100]))
    assert len(r.episodes) == 1
    e = r.episodes[0]
    assert e.left_truncated and e.recovered and e.recovery_sessions is None
    assert metric(r, "recovered_conditional_median").value is None
    assert r.recovery_survival == []


def test_km_same_day_recovery_before_censor():
    # Two independent events: one recovers at duration 2, another censored at 2.
    r = measure_underlying_profile(make_input([95, 94, 100, 100, 95, 94, 93]))
    p = r.recovery_survival[0]
    assert (p.elapsed_sessions, p.at_risk, p.recoveries, p.censored) == (2, 2, 1, 1)
    assert p.survival == .5
    assert metric(r, "recovery_km_median").value == 2


def test_future_data_does_not_change_past_any_metric_or_identity():
    inp = make_input([95, 90, 100])
    future = DailyBar(session="2026-09-08", close=1e9, volume=-10)
    changed = inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={"bars": inp.feature_view.bars+[future]})})
    assert measure_underlying_profile(inp) == measure_underlying_profile(changed)


def test_gap_uses_previous_atr_even_when_current_range_explodes():
    inp = make_input()
    bars = inp.feature_view.bars.copy()
    bars[-1] = DailyBar(session=bars[-1].session, open=96, high=150, low=50, close=100, volume=1000)
    r = measure_underlying_profile(inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={"bars": bars})}))
    g = r.gap_observations[-1]
    assert g.previous_close == 100 and g.atr_previous_session == 2
    assert g.down_gap_atr == 2 and g.up_gap_atr == 0
    assert metric(r, "gap_down_ge_1.5_count_20").value == 1


def test_missing_volume_and_benchmark_only_block_dependent_metrics():
    r = measure_underlying_profile(make_input(volume=None, benchmark=False))
    assert metric(r, "dollar_volume_median_20").value is None
    assert metric(r, "relative_strength_20").value is None
    assert metric(r, "realized_volatility_20").value == 0
    assert metric(r, "atr_over_price").value == .02
    assert metric(r, "gap_down_q95_252").value == 0


def test_60_bars_not_252_or_60_returns_but_short_window_valid():
    r = measure_underlying_profile(make_input(size=60))
    assert len(r.coverage.actual_sessions) == 60
    assert len(r.coverage.missing_analysis_sessions) == 192
    assert metric(r, "realized_volatility_20").value == 0
    assert metric(r, "relative_strength_20").value == 0
    assert metric(r, "realized_volatility_60").value is None
    assert metric(r, "relative_strength_60").value is None
    assert metric(r, "gap_down_q95_252").value is None
    assert metric(r, "drawdown_depth_q95").value is None


def test_missing_session_does_not_compress_return_or_followup_time():
    inp = make_input([95, 94, 100])
    bars = inp.feature_view.bars[:-2]+inp.feature_view.bars[-1:]
    r = measure_underlying_profile(inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={"bars": bars})}))
    assert metric(r, "realized_volatility_20").value is None
    assert r.episodes[0].right_censored
    assert "MISSING_CLOSE_INTERRUPTS_FOLLOWUP" in r.episodes[0].reason_codes


def test_request_before_close_rejected_and_after_close_allowed():
    inp = make_input()
    ctx = inp.call_context.model_copy(update={"requested_as_of": "2026-09-04T15:59:59-04:00", "mode": "EOD"})
    with pytest.raises(ValueError, match="DAILY_BAR_NOT_COMPLETED"):
        measure_underlying_profile(inp.model_copy(update={"call_context": ctx}))
    ctx = ctx.model_copy(update={"requested_as_of": "2026-09-05T00:01:00+04:00"})
    assert measure_underlying_profile(inp.model_copy(update={"call_context": ctx})).time_context.completed_daily_bar


def test_identity_and_duplicates_rejected():
    inp = make_input()
    with pytest.raises(ValueError, match="UNVERIFIED_SOURCE"):
        measure_underlying_profile(inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={"input_kind": "VERIFIED_CANONICAL"})}))
    with pytest.raises(ValueError, match="NOT_UNIQUE_SORTED"):
        measure_underlying_profile(inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={"bars": inp.feature_view.bars*2})}))
    with pytest.raises(ValidationError):
        ProfilePolicy(schema_version="9.0")


def test_empty_input_not_zero_events_or_success():
    inp = make_input(size=0)
    r = measure_underlying_profile(inp)
    assert r.profile_status == "MISSING"
    assert metric(r, "episode_count").value is None
    assert all(m.value is None for m in r.measurements)


def test_short_benchmark_does_not_destroy_valid_twenty_day_strength():
    inp = make_input()
    benchmark = inp.benchmark.model_copy(update={"bars": inp.benchmark.bars[-21:]})
    r = measure_underlying_profile(inp.model_copy(update={"benchmark": benchmark}))
    assert metric(r, "relative_strength_20").value == 0
    assert metric(r, "relative_strength_60").value is None
    assert metric(r, "dollar_volume_median_20").value == 100000


def test_missing_current_price_does_not_return_stale_atr_ratio():
    inp = make_input()
    bars = inp.feature_view.bars.copy()
    bars[-1] = bars[-1].model_copy(update={"close": None})
    r = measure_underlying_profile(inp.model_copy(update={"feature_view": inp.feature_view.model_copy(update={"bars": bars})}))
    assert metric(r, "atr_over_price").value is None
    assert metric(r, "realized_volatility_20").value is None
    # Opening gap does not require the later close.
    assert r.gap_observations[-1].down_gap_atr == 0


def test_views_roundtrip_and_policy_recompute_boundary(tmp_path):
    inp = make_input([95, 90, 100])
    r = measure_underlying_profile(inp)
    changed_request = inp.model_copy(update={"call_context": inp.call_context.model_copy(update={"request_id": "other", "run_id": "other"})})
    assert measure_underlying_profile(changed_request).result_id == r.result_id
    changed_policy = inp.model_copy(update={"effective_policy": ProfilePolicy(atr_period=20, parameter_source="REQUEST")})
    assert measure_underlying_profile(changed_policy).result_id != r.result_id
    assert "冻结高点" in underlying_profiles_to_markdown([r])
    assert underlying_profile_to_ai_view(r)["measurements"] == [m.model_dump(mode="json") for m in r.measurements]
    root = write_underlying_profile_artifacts(tmp_path / "out", [r])
    stored = json.loads((root/"underlying_profiles.json").read_text(encoding="utf-8"))[0]
    assert UnderlyingProfile.model_validate(stored) == r
    from hashlib import sha256
    manifest = json.loads((root/"artifact_manifest.json").read_text())
    assert all(sha256((root/name).read_bytes()).hexdigest() == digest for name, digest in manifest["sha256"].items())
    with pytest.raises(ValueError, match="NOT_EMPTY"):
        write_underlying_profile_artifacts(root, [r])


def test_received_at_is_audit_metadata_not_semantic_identity():
    inp = make_input([95, 90, 100])
    first_view = inp.feature_view.model_copy(update={"received_at": "2026-09-05T00:00:01Z"})
    second_view = inp.feature_view.model_copy(update={"received_at": "2026-09-05T00:05:00Z"})
    first = measure_underlying_profile(inp.model_copy(update={"feature_view": first_view}))
    second = measure_underlying_profile(inp.model_copy(update={"feature_view": second_view}))

    assert first.time_context.received_at == "2026-09-05T00:00:01Z"
    assert second.time_context.received_at == "2026-09-05T00:05:00Z"
    assert first.measurements == second.measurements
    assert first.episodes == second.episodes
    assert first.result_id == second.result_id
