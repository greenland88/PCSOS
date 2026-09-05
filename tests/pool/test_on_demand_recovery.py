"""Isolated fixture tests; provider responses here are NOT real acceptance evidence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import perf_counter
from types import SimpleNamespace
import json

import pandas as pd
import pytest

from pcs.data.access import PCSDataAccess
from pcs.data import control_plane as cp
from pcs.data.strategy_readiness import resolve_active_verified_options_handle
from pcs.pool import runner
from pcs.pool.artifacts import CandidateCheckpoints
from pcs.pool.models import TimingStatus, OptionsStatus, FinalAction
from pcs.pool.options import load_pool_option_rules
from pcs.pool.runtime import PoolRuntime


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    rules = load_pool_option_rules()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/market_data_source_registry.yaml").write_text(
        "sources:\n  options:\n    - source_id: clickhouse_options\n      authorized: true\n      enabled: true\n")
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "data/manifest.csv", parquet_root=tmp_path / "data/parquet")
    dates = pd.date_range("2026-01-01", "2026-09-08")
    for symbol in ("AAA", "BBB", "QQQ"):
        daily = pd.DataFrame({"symbol": symbol, "date": dates, "open": 100., "high": 101.,
                              "low": 99., "close": 100., "volume": 10000})
        access.promote_generation(daily, "daily", symbol, "year=2026", source_version="isolated-test")
    calls = []
    def trend(*args, **kwargs):
        calls.append(kwargs["symbol"])
        return SimpleNamespace(available=True, warnings=(), support=SimpleNamespace(current_atr=2.),
            market_structure_engine=SimpleNamespace(feature_max_date=kwargs["as_of_date"],
                structural_trend="STRUCTURAL_UPTREND", short_term_phase="RECLAIM_CONFIRMED"))
    monkeypatch.setattr(runner, "build_trend_snapshot", trend)
    for name in ("interpret_trend", "score_trend"):
        monkeypatch.setattr(runner, name, lambda *a, **k: SimpleNamespace(available=True, warnings=(), reasons=()))
    monkeypatch.setattr(runner, "evaluate_trend_gate", lambda *a, **k: SimpleNamespace(available=True, trend_gate_result="PASS", reasons=()))
    monkeypatch.setattr(runner, "evaluate_pullback_gate", lambda *a, **k: SimpleNamespace(available=True, pullback_gate_result="PASS", reasons=()))
    def run(symbols=("AAA",), prepare=False, asof="2026-09-04T17:00:00-04:00", **kwargs):
        return runner.run_pcs_pool(symbols=symbols, mode="EOD", as_of=asof, data_access=access,
            data_mode="PREPARE_THEN_SCAN" if prepare else "READ_ONLY", auto_prepare_data=prepare,
            option_rules=rules, output_directory=tmp_path / "runs", max_workers=2,
            static_metadata_reader=lambda s: {"optionable": True}, **kwargs)
    return access, run, calls, rules


def chain(symbol="AAA", day="2026-09-04"):
    return pd.DataFrame({"symbol": [symbol]*2, "trade_date": [pd.Timestamp(day)]*2,
        "expiration_date": [pd.Timestamp(day)+pd.Timedelta(days=35)]*2,
        "strike": [90., 85.], "call_put": ["p", "p"], "bid": [2., .5], "ask": [2.2, .7],
        "volume": [1000, 1000], "open_interest": [1000, 1000]})


def provider(monkeypatch, *, empty=(), both_sides=False):
    calls = []
    class Client:
        def fetch_options_coverage(self, symbol, start, end):
            calls.append((symbol, start, end))
            return {"status": "BLOCKED" if symbol in empty else "READY", "physical_rows": 0 if symbol in empty else 2,
                "requested_start": start, "requested_end": end,
                "reason_codes": ["AUTHORIZED_SOURCE_NO_ROWS"] if symbol in empty else []}
        def fetch_options_range(self, symbol, start, end):
            puts = chain(symbol, start)
            return pd.concat([puts, puts.assign(call_put="c")], ignore_index=True) if both_sides else puts
    original = cp.default_import_handlers
    monkeypatch.setattr(cp, "default_import_handlers", lambda **kw: original(**kw, clickhouse_client=Client()))
    return calls


def test_readonly_keeps_candidate_without_import_or_promotion(bundle, monkeypatch):
    access, run, timing, _ = bundle
    def forbidden(*a, **k):
        pytest.fail("READ_ONLY attempted provider/import/promotion")
    monkeypatch.setattr(runner, "ensure_market_data", forbidden)
    monkeypatch.setattr(PCSDataAccess, "promote_generation", forbidden)
    before = access.manifest_path.read_bytes()
    row = run().ticker_results[0]
    assert row.timing_status == TimingStatus.TIMING_ENTRY_READY
    assert row.options_status == OptionsStatus.DATA_BLOCKED
    assert row.final_action == FinalAction.WAIT
    assert row.candidate_state["requirements"]["required_start"] == "2026-09-04"
    assert row.candidate_state["requirements"]["required_end"] == "2026-09-04"
    assert row.candidate_state["expiration_end"] >= "2026-10-01"
    assert access.manifest_path.read_bytes() == before


def test_standard_loader_verified_read_and_same_run_options(bundle, monkeypatch):
    access, run, timing, _ = bundle
    calls = provider(monkeypatch)
    result = run(prepare=True)
    row = result.ticker_results[0]
    assert calls == [("AAA", "2026-09-04", "2026-09-04")]
    assert timing == ["AAA"]
    assert result.counters["provider_calls"] == 1
    assert result.counters["promotion_calls"] == 1
    assert row.options_status == OptionsStatus.DISCOVERED
    assert row.spread_count == 1 and row.candidate_state["verified_read_status"] == "PASS"
    assert row.candidate_state["preparation_receipt"]["promoted_partitions"]
    assert row.portfolio_status == "NOT_EVALUATED" and row.final_action != FinalAction.PCS_TRADE_READY
    handle = resolve_active_verified_options_handle("AAA", "2026-09-04", data_access=access)
    assert len(access.read_verified_dataset(handle, start_date="2026-09-04", end_date="2026-09-04")) == 2
    second = run(prepare=True)
    assert timing == ["AAA"] and len(calls) == 1
    assert second.summary["timing_reused_count"] == 1
    assert second.summary["options_prepare_attempted_count"] == 0
    assert second.ticker_results[0].candidate_state["options_evaluation_reused"] is True


def test_zero_rows_wait_with_backoff_and_other_symbol_continues(bundle, monkeypatch):
    _, run, timing, _ = bundle
    calls = provider(monkeypatch, empty=("AAA",))
    first = run(symbols=("AAA", "BBB"), prepare=True)
    a, b = first.ticker_results
    assert a.timing_status == TimingStatus.TIMING_ENTRY_READY and a.next_review_at
    assert "AUTHORIZED_SOURCE_NO_ROWS" in a.reason_codes
    assert a.candidate_state["source_query_completed_at"]
    assert b.options_status == OptionsStatus.DISCOVERED
    run(symbols=("AAA", "BBB"), prepare=True)
    assert len(calls) == 2 and sorted(timing) == ["AAA", "BBB"]


def test_loader_success_without_verified_data_never_evaluates_contracts(bundle, monkeypatch):
    _, run, _, _ = bundle
    monkeypatch.setattr(runner, "ensure_market_data", lambda *a, **k: {"status": "SUCCESS", "reason_codes": []})
    monkeypatch.setattr(runner, "discover_spreads", lambda *a, **k: pytest.fail("unverified options evaluated"))
    row = run(prepare=True).ticker_results[0]
    assert row.timing_status == TimingStatus.TIMING_ENTRY_READY
    assert row.options_status == OptionsStatus.DATA_BLOCKED
    assert row.candidate_state["verified_read_status"] == "FAILED"


def test_checkpoint_invalidates_new_session_and_rules(bundle, monkeypatch):
    _, run, timing, rules = bundle
    provider(monkeypatch, empty=("AAA",))
    run()
    run()
    assert timing == ["AAA"]
    run(asof="2026-09-08T17:00:00-04:00")
    assert timing == ["AAA", "AAA"]
    # Compatibility is based on actual code/rule identities, not run IDs.
    original = runner._execution_identity
    monkeypatch.setattr(runner, "_execution_identity", lambda r: (original(r)[0], "different-rules"))
    run(asof="2026-09-08T17:00:00-04:00")
    assert len(timing) == 3


def test_tampered_checkpoint_not_consumed(bundle):
    _, run, timing, _ = bundle
    first = run()
    path = next(Path("runs").glob("*/candidate_checkpoints/*.json"))
    payload = json.loads(path.read_text())
    payload["payload"]["row"]["candidate_state"]["atr"] = 0
    path.write_text(json.dumps(payload))
    run()
    assert len(timing) == 2


def test_same_need_singleflight_and_cross_runtime_lease(bundle, monkeypatch):
    access, run, _, rules = bundle
    row = run().ticker_results[0]
    runtime = PoolRuntime(access=access, options_handle_resolver=resolve_active_verified_options_handle)
    entered, release = Event(), Event()
    calls = []
    def load(*a, **k):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return {"status": "BLOCKED", "reason_codes": ["AUTHORIZED_SOURCE_NO_ROWS"]}
    monkeypatch.setattr(runner, "ensure_market_data", load)
    req = runner._options_requirement(row, rules)
    def prepare(rt):
        return runner._prepare_candidate_options(row, runtime=rt, requirements=req)
    with ThreadPoolExecutor(max_workers=3) as pool:
        a = pool.submit(prepare, runtime)
        assert entered.wait(3)
        b = pool.submit(prepare, runtime)
        other = PoolRuntime(access=access, options_handle_resolver=resolve_active_verified_options_handle)
        busy = prepare(other)
        assert busy["reason_codes"] == ["OPTIONS_PREPARATION_IN_PROGRESS"]
        release.set()
        assert a.result() == b.result()
    assert calls == [1]


def test_options_interior_gap_uses_requested_quote_not_latest(bundle, monkeypatch):
    access, run, _, _ = bundle
    access.promote_generation(chain(day="2026-09-03"), "options", "AAA", "year=2026/quarter=3", source_version="isolated-test")
    access.promote_generation(chain(day="2026-09-08"), "options", "AAA", "year=2026/quarter=3", source_version="isolated-test")
    calls = provider(monkeypatch)
    promotions = []
    original = cp.ImportEngine.promote
    def capture(self, *a, **k):
        value = original(self, *a, **k)
        promotions.append(value)
        return value
    monkeypatch.setattr(cp.ImportEngine, "promote", capture)
    row = run(prepare=True).ticker_results[0]
    assert calls == [("AAA", "2026-09-04", "2026-09-04")]
    assert row.options_status == OptionsStatus.DISCOVERED, promotions
    assert row.candidate_state["verified_read_status"] == "PASS"


def test_first_canonical_ingestion_has_no_read_route_until_promoted(bundle, monkeypatch):
    _, _, _, _ = bundle
    calls = provider(monkeypatch, both_sides=True)
    access = PCSDataAccess.canonical()
    with pytest.raises(Exception, match="DATA_NOT_INGESTED_OR_CANONICAL_MANIFEST_MISSING"):
        access._resolve_route("options", "NEW")
    req = cp.MarketDataRequirements(symbol="NEW", required_start="2026-09-04", required_end="2026-09-04",
        datasets=("options",), decision_as_of="2026-09-04", option_type="PUT", min_dte=30, max_dte=45, required_history_rows=0)
    result = cp.ensure_market_data("NEW", req, access=access)
    assert calls == [("NEW", "2026-09-04", "2026-09-04")]
    assert result.status in {"READY", "ALREADY_COMPLETE"}, result.reason_codes
    handle = resolve_active_verified_options_handle("NEW", "2026-09-04", data_access=access)
    assert len(access.read_verified_dataset(handle)) == 4


def test_options_identity_change_reuses_timing_only(bundle, monkeypatch):
    access, run, timing, _ = bundle
    provider(monkeypatch)
    first = run(prepare=True).ticker_results[0]
    replacement = chain().assign(bid=[2.1, .5])
    access.promote_generation(replacement, "options", "AAA", "year=2026/quarter=3", source_version="isolated-update")
    second = run().ticker_results[0]
    assert timing == ["AAA"]
    assert second.candidate_state["options_identity"] != first.candidate_state["options_identity"]
    assert second.candidate_state["options_evaluation_reused"] is False
    assert second.discovered_contracts[0]["bid_credit"] != first.discovered_contracts[0]["bid_credit"]


def test_code_invalidation_preserves_source_backoff(bundle, monkeypatch):
    _, run, timing, _ = bundle
    calls = provider(monkeypatch, empty=("AAA",))
    first = run(prepare=True).ticker_results[0]
    original = runner._execution_identity
    monkeypatch.setattr(runner, "_execution_identity", lambda r: ("changed-code", original(r)[1]))
    second = run(prepare=True).ticker_results[0]
    assert len(timing) == 2 and len(calls) == 1
    assert second.next_review_at == first.next_review_at
    assert second.candidate_state["preparation_receipt"] == first.candidate_state["preparation_receipt"]


def test_configuration_not_loaded_is_distinct_from_source_empty(bundle, monkeypatch):
    access, _, _, _ = bundle
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    monkeypatch.setenv("PCS_ENV_FILE", "missing-authorized-env")
    req = cp.MarketDataRequirements("AAA", "2026-09-04", "2026-09-04", datasets=("options",))
    result = cp.default_import_handlers(access=access)["options"](req)
    assert result["reason_codes"] == ["CONFIGURATION_NOT_LOADED"]
    assert runner._source_check_status(result) == "CONFIGURATION_NOT_LOADED"


@pytest.mark.parametrize("code,expected", [
    ("AUTHORIZED_SOURCE_NO_ROWS", "CONFIRMED_EMPTY"),
    ("CLICKHOUSE_AUTHENTICATION_FAILED", "AUTHENTICATION_FAILED"),
    ("PROVIDER_PROBE_TIMEOUT", "TIMED_OUT"),
])
def test_source_status_does_not_collapse_failures(code, expected):
    assert runner._source_check_status({"reason_codes": [code]}) == expected


def test_read_failure_invalidates_old_options_identity_without_losing_timing(bundle, monkeypatch):
    _, run, timing, _ = bundle
    provider(monkeypatch)
    run(prepare=True)
    def invalid(*a, **k):
        raise ValueError("DATASET_CHECKSUM_MISMATCH")
    monkeypatch.setattr(runner, "resolve_active_verified_options_handle", invalid)
    result = run()
    row = result.ticker_results[0]
    assert len(timing) == 1 and row.timing_status == TimingStatus.TIMING_ENTRY_READY
    assert row.options_status == OptionsStatus.DATA_BLOCKED
    assert row.candidate_state["options_identity"] is None
    assert row.candidate_state["verified_read_status"] == "FAILED"
    assert result.summary["options_verified_count"] == 0
