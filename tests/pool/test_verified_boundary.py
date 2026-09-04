"""Real generation/route reads with controlled timing, never production adapters."""
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from pcs.data.access import PCSDataAccess
from pcs.data.strategy_readiness import resolve_active_verified_daily_handle, resolve_active_verified_options_handle
from pcs.pool.models import EligibilityStatus, FinalAction, OptionsStatus, TimingStatus
from pcs.pool.options import load_pool_option_rules
from pcs.pool.runner import _evaluate_symbol
from pcs.pool.runtime import ManifestSnapshot, PoolRuntime


@pytest.fixture(params=["options", "options_v2", "options_v3"])
def routed_bundle(tmp_path, request):
    day = pd.Timestamp("2025-09-17")
    daily = pd.DataFrame({"symbol": "AAA", "date": pd.date_range("2025-01-01", day),
                          "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1000})
    chain = pd.DataFrame({"symbol": ["AAA"] * 2, "trade_date": [day] * 2,
                          "expiration_date": [day + pd.Timedelta(days=35)] * 2,
                          "strike": [90., 85.], "call_put": ["p", "p"],
                          "bid": [2., .5], "ask": [2.2, .7],
                          "volume": [1000, 1000], "open_interest": [1000, 1000]})
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "daily.csv", parquet_root=tmp_path / "daily")
    access.promote_generation(daily, "daily", "AAA", "year=2025", source_version="test-fixture")
    options = PCSDataAccess.isolated(manifest_path=tmp_path / "options.csv", parquet_root=tmp_path / "options")
    options.promote_generation(chain, request.param, "AAA", "year=2025/quarter=3", source_version="test-fixture")
    original_route = access._resolve_route
    calls = []
    def route(dataset, symbol):
        calls.append((dataset, symbol))
        if dataset == "options":
            return request.param, options.manifest_path, options.parquet_root
        return original_route(dataset, symbol)
    access._resolve_route = route
    return access, options, day, daily, request.param, calls


def _timing_ready(monkeypatch, day):
    trend = SimpleNamespace(available=True, warnings=(), support=SimpleNamespace(current_atr=2.),
                            market_structure_engine=SimpleNamespace(feature_max_date=day,
                            structural_trend="STRUCTURAL_UPTREND", short_term_phase="RECLAIM_CONFIRMED"))
    monkeypatch.setattr("pcs.pool.runner.build_trend_snapshot", lambda *a, **k: trend)
    for name in ("interpret_trend", "score_trend"):
        monkeypatch.setattr(f"pcs.pool.runner.{name}", lambda *a, **k:
                            SimpleNamespace(available=True, warnings=(), reasons=()))
    monkeypatch.setattr("pcs.pool.runner.evaluate_trend_gate", lambda *a, **k:
                        SimpleNamespace(available=True, trend_gate_result="PASS", warnings=(), reasons=()))
    monkeypatch.setattr("pcs.pool.runner.evaluate_pullback_gate", lambda *a, **k:
                        SimpleNamespace(available=True, pullback_gate_result="PASS", warnings=(), reasons=()))


def _evaluate(bundle, monkeypatch):
    access, _, day, daily, _, _ = bundle
    _timing_ready(monkeypatch, day)
    runtime = PoolRuntime(access=access, daily_handle_resolver=resolve_active_verified_daily_handle,
                           options_handle_resolver=resolve_active_verified_options_handle)
    return _evaluate_symbol("AAA", run_id="fixture", asof=str(day.date()), access=access,
        benchmark=daily, benchmark_symbol="QQQ", options_reader=None, option_rules=load_pool_option_rules(),
        runtime=runtime, options_enabled=True, static_metadata_reader=lambda _: {"optionable": True})


def test_verified_options_route_discovers_real_spread(routed_bundle, monkeypatch):
    row = _evaluate(routed_bundle, monkeypatch)
    assert row.timing_status == TimingStatus.TIMING_ENTRY_READY
    # Stage B currently means DISCOVERED, not Stage C approval. Do not fabricate
    # event/portfolio/selection approval to satisfy an obsolete PASS assertion.
    assert row.options_status == OptionsStatus.DISCOVERED
    assert row.spread_count == 1
    assert row.discovered_contracts[0]["short_strike"] == 90.
    assert row.discovered_contracts[0]["long_strike"] == 85.
    assert row.discovered_contracts[0]["bid_credit"] == pytest.approx(1.3)
    assert row.final_action != FinalAction.PCS_TRADE_READY
    assert ("options", "AAA") in routed_bundle[-1]


def test_wrong_manifest_snapshot_is_not_reused(routed_bundle):
    access, options, day, _, physical, _ = routed_bundle
    wrong = ManifestSnapshot.capture(access)
    handle = resolve_active_verified_options_handle("AAA", str(day.date()), data_access=access,
                                                     manifest_snapshot=wrong)
    assert handle.dataset == physical
    assert Path(handle.manifest_identity) == options.manifest_path.resolve()
    assert len(access.read_verified_dataset(handle)) == 2


@pytest.mark.parametrize("offset,valid", [(-1, False), (0, True), (1, False)])
def test_options_date_window_is_exact(routed_bundle, offset, valid):
    access, _, day, *_ = routed_bundle
    date = str((day + pd.Timedelta(days=offset)).date())
    if valid:
        assert resolve_active_verified_options_handle("AAA", date, data_access=access)
    else:
        with pytest.raises(ValueError, match="OPTIONS_GENERATION_MISSING"):
            resolve_active_verified_options_handle("AAA", date, data_access=access)


def test_bad_options_preserves_timing_and_original_reason(routed_bundle, monkeypatch):
    _, options, _, _, _, _ = routed_bundle
    manifest = pd.read_csv(options.manifest_path)
    manifest["content_hash"] = "invalid-fixture-hash"
    manifest.to_csv(options.manifest_path, index=False)
    row = _evaluate(routed_bundle, monkeypatch)
    assert row.eligibility_status == EligibilityStatus.PCS_ELIGIBLE
    assert row.timing_status == TimingStatus.TIMING_ENTRY_READY
    assert row.options_status == OptionsStatus.DATA_BLOCKED
    assert row.spread_count == 0
    assert not row.discovered_contracts
    assert any("CHECKSUM" in code for code in row.reason_codes)
    assert not any("UnboundLocalError" in code for code in row.reason_codes)
