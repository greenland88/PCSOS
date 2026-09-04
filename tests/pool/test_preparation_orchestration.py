from types import SimpleNamespace

import pandas as pd
import pytest

import pcs.pool.runner as runner
from pcs.pool.models import EligibilityStatus
from pcs.pool.runtime import ManifestSnapshot
from pcs.data.access import PCSDataAccess
from pcs.data.strategy_readiness import resolve_active_verified_daily_handle
from pcs.data.canonical_generations import admit_migrated_daily_symbol


def _frame():
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    close = pd.Series(range(100, 360), dtype=float)
    return pd.DataFrame({"symbol": "AAA", "date": dates, "open": close,
                         "high": close + 1, "low": close - 1,
                         "close": close, "volume": 1000})


class RoutedFixtureAccess:
    def __init__(self, tmp_path, ready=()):
        self.manifest_path = tmp_path / "manifest.csv"
        self.parquet_root = tmp_path / "parquet"
        self.ready = set(ready)
        self.frame = _frame()

    def _resolve_route(self, dataset, symbol):
        return dataset, self.manifest_path, self.parquet_root

    def _read_manifest(self, _path):
        rows = []
        for symbol in sorted(self.ready):
            rows.append({"dataset": "daily", "symbol": symbol,
                         "active_generation": f"gen-{symbol}",
                         "min_date": "2025-01-01", "max_date": "2025-09-17",
                         "row_count": 260, "status": "SUCCESS"})
        return pd.DataFrame(rows)

    def read_verified_dataset(self, handle, **_kwargs):
        frame = self.frame.copy()
        frame["symbol"] = str(handle.ticker).upper()
        return frame


def _resolver(symbol, as_of, warmup, *, data_access, manifest_snapshot=None):
    state = data_access._read_manifest(data_access.manifest_path)
    if str(symbol).upper() not in set(state.get("symbol", ())):
        raise ValueError("ACTIVE_GENERATION_MISSING")
    return SimpleNamespace(ticker=str(symbol).upper(), generation_id=f"gen-{symbol}",
                           verification_status="VERIFIED")


def _patch_timing(monkeypatch):
    trend = SimpleNamespace(available=True, warnings=(), market_structure_engine=SimpleNamespace(
        feature_max_date="2025-09-01", structural_trend="STRUCTURAL_UPTREND",
        short_term_phase="RECLAIM_CONFIRMED"), support=SimpleNamespace(current_atr=2))
    interpretation = SimpleNamespace(available=True, warnings=(), reasons=())
    score = SimpleNamespace(available=True, warnings=(), reasons=())
    monkeypatch.setattr(runner, "build_trend_snapshot", lambda *a, **k: trend)
    monkeypatch.setattr(runner, "interpret_trend", lambda *a, **k: interpretation)
    monkeypatch.setattr(runner, "score_trend", lambda *a, **k: score)
    monkeypatch.setattr(runner, "evaluate_trend_gate", lambda *a, **k: SimpleNamespace(
        available=True, trend_gate_result="PASS", reasons=(), warnings=()))
    monkeypatch.setattr(runner, "evaluate_pullback_gate", lambda *a, **k: SimpleNamespace(
        available=True, pullback_gate_result="PASS", reasons=(), warnings=()))


def test_prepare_then_scan_revalidates_and_freezes_after_preparation(tmp_path, monkeypatch):
    access = RoutedFixtureAccess(tmp_path)
    calls = []

    def controlled_ensure(symbol, requirements, *, access):
        assert requirements.datasets == ("daily",)
        assert requirements.required_history_rows == 200
        assert requirements.required_end == "2025-09-17"
        calls.append((symbol, requirements))
        access.ready.add(str(symbol).upper())
        return SimpleNamespace(status="READY", reason_codes=(), provider_coverage=({"source": "fixture"},),
                               promoted_partitions=({"dataset": "daily"},))

    monkeypatch.setattr(runner, "ensure_market_data", controlled_ensure)
    _patch_timing(monkeypatch)
    result = runner.run_pcs_pool(
        symbols=["AAA"], benchmark_symbol="QQQ", as_of="2025-09-18",
        mode="PREMARKET", data_mode="PREPARE_THEN_SCAN", data_access=access,
        daily_handle_resolver=_resolver, static_metadata_reader=lambda _: {"optionable": True},
        max_data_workers=1)
    row = result.ticker_results[0]
    assert row.preparation_status == "PREPARED_READY"
    assert row.eligibility_status == EligibilityStatus.PCS_ELIGIBLE
    assert result.summary["daily_prepare_attempted_count"] == 2  # AAA and QQQ
    assert result.summary["daily_prepared_ready_count"] == 2
    assert result.snapshot.manifest_snapshot_id == ManifestSnapshot.capture(access).identity
    assert result.counters["provider_calls"] == 0
    assert result.counters["recovery_calls"] == 2
    assert len(calls) == 2


def test_read_only_reports_preparation_required_without_writes(tmp_path, monkeypatch):
    access = RoutedFixtureAccess(tmp_path)
    calls = []
    monkeypatch.setattr(runner, "ensure_market_data", lambda *a, **k: calls.append(1))
    result = runner.run_pcs_pool(
        symbols=["AAA"], benchmark_symbol="QQQ", as_of="2025-09-18",
        mode="PREMARKET", data_mode="READ_ONLY", data_access=access,
        daily_handle_resolver=_resolver)
    row = result.ticker_results[0]
    assert not calls
    assert row.preparation_status == "READ_ONLY_NOT_PREPARED"
    assert row.preparation_attempted is False
    assert "BENCHMARK_PREP_REQUIRED" in row.reason_codes
    assert result.summary["daily_prepare_attempted_count"] == 0


def test_preparation_failure_isolated_from_ready_ticker(tmp_path, monkeypatch):
    access = RoutedFixtureAccess(tmp_path, ready=("GOOD", "QQQ"))

    def controlled_ensure(symbol, requirements, *, access):
        assert requirements.datasets == ("daily",)
        return SimpleNamespace(status="BLOCKED", reason_codes=("SOURCE_UNAVAILABLE",),
                               provider_coverage=(), promoted_partitions=())

    monkeypatch.setattr(runner, "ensure_market_data", controlled_ensure)
    _patch_timing(monkeypatch)
    result = runner.run_pcs_pool(
        symbols=["GOOD", "BAD"], benchmark_symbol="QQQ", as_of="2025-09-18",
        mode="PREMARKET", data_mode="PREPARE_THEN_SCAN", data_access=access,
        daily_handle_resolver=_resolver, static_metadata_reader=lambda _: {"optionable": True},
        max_data_workers=2)
    rows = {row.symbol: row for row in result.ticker_results}
    assert rows["GOOD"].preparation_status == "NOT_NEEDED"
    assert rows["GOOD"].eligibility_status == EligibilityStatus.PCS_ELIGIBLE
    assert rows["BAD"].preparation_status == "PREPARATION_FAILED"
    assert "SOURCE_UNAVAILABLE" in rows["BAD"].preparation_reason_codes
    assert result.summary["daily_scan_ready_count"] == 1


def test_preparation_worker_count_is_bounded(tmp_path, monkeypatch):
    access = RoutedFixtureAccess(tmp_path)
    active = 0
    maximum = 0

    def controlled_ensure(symbol, requirements, *, access):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        access.ready.add(str(symbol).upper())
        active -= 1
        return SimpleNamespace(status="READY", reason_codes=(), provider_coverage=(), promoted_partitions=())

    monkeypatch.setattr(runner, "ensure_market_data", controlled_ensure)
    runner._bounded_daily_preparation(
        ["A", "B", "C", "D"], access, "2025-09-17", max_workers=2, timeout_seconds=5)
    assert maximum <= 2


def test_valid_legacy_daily_is_formally_admitted_and_idempotent(tmp_path):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    frame = _frame()
    path = access.parquet_root / "daily" / "symbol=AAA" / "year=2025" / "AAA_2025.parquet"
    path.parent.mkdir(parents=True)
    frame.to_parquet(path, index=False)
    manifest = pd.DataFrame([{
        "dataset": "daily", "symbol": "AAA", "year": 2025, "quarter": None,
        "parquet_path": str(path), "row_count": len(frame),
        "min_date": str(frame.date.min().date()), "max_date": str(frame.date.max().date()),
        "active_generation": None, "file_hash": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
        "status": "SUCCESS",
    }])
    manifest.to_csv(access.manifest_path, index=False)
    assert runner._daily_preflight(["AAA"], access, "2025-09-17")["AAA"].reason_codes == ("ACTIVE_GENERATION_MISSING",)
    first_admission = admit_migrated_daily_symbol(
        "AAA", decision_as_of="2025-09-17", data_access=access,
        migration_manifest_path=tmp_path / "migration.csv")
    assert first_admission["status"] == "MIGRATION_CATALOG_MISSING"
    migration = manifest.copy()
    migration["status"] = "SUCCESS"
    migration.to_csv(tmp_path / "migration.csv", index=False)
    first_admission = admit_migrated_daily_symbol(
        "AAA", decision_as_of="2025-09-17", data_access=access,
        migration_manifest_path=tmp_path / "migration.csv")
    assert first_admission["status"] == "ADMITTED_READY"
    first = resolve_active_verified_daily_handle("AAA", "2025-09-17", 200, data_access=access)
    manifest_hash = __import__("hashlib").sha256(access.manifest_path.read_bytes()).hexdigest()
    assert admit_migrated_daily_symbol(
        "AAA", decision_as_of="2025-09-17", data_access=access,
        migration_manifest_path=tmp_path / "migration.csv")["status"] == "ALREADY_ADMITTED"
    assert __import__("hashlib").sha256(access.manifest_path.read_bytes()).hexdigest() == manifest_hash
    second = resolve_active_verified_daily_handle("AAA", "2025-09-17", 200, data_access=access)
    assert second.generation_id == first.generation_id


def test_corrupt_migrated_daily_file_is_not_admitted(tmp_path):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    frame = _frame()
    frame.loc[0, "high"] = frame.loc[0, "low"] - 1
    path = access.parquet_root / "daily" / "symbol=AAA" / "year=2025" / "AAA_2025.parquet"
    path.parent.mkdir(parents=True)
    frame.to_parquet(path, index=False)
    migration = pd.DataFrame([{"symbol": "AAA", "status": "SUCCESS"}])
    migration.to_csv(tmp_path / "migration.csv", index=False)
    result = admit_migrated_daily_symbol(
        "AAA", decision_as_of="2025-09-17", data_access=access,
        migration_manifest_path=tmp_path / "migration.csv")
    assert result["status"] == "MIGRATED_CANONICAL_INVALID"
    assert result["reason_codes"] == ("MIGRATED_CANONICAL_OHLCV_INVALID",)
    assert not list((path.parent / "generations").glob("*.parquet"))


def test_quarterly_warmup_uses_cumulative_active_rows(tmp_path):
    access = RoutedFixtureAccess(tmp_path)
    access.ready.add("AAA")
    rows = []
    for quarter, count, start, end, generation in (
            ("2025Q4", 63, "2025-10-01", "2025-12-31", "g1"),
            ("2026Q1", 62, "2026-01-01", "2026-03-31", "g2"),
            ("2026Q2", 63, "2026-04-01", "2026-06-30", "g3"),
            ("2026Q3", 45, "2026-07-01", "2026-09-03", "g4")):
        rows.append({"dataset": "daily", "symbol": "AAA", "partition_ids": quarter,
                     "active_generation": generation, "min_date": start, "max_date": end,
                     "row_count": count, "status": "SUCCESS"})
    access._read_manifest = lambda _path: pd.DataFrame(rows)
    state = runner._daily_preflight(["AAA"], access, "2026-09-03")["AAA"]
    assert state.status == "READY"


def test_quarterly_warmup_below_threshold_requires_preparation(tmp_path):
    access = RoutedFixtureAccess(tmp_path)
    rows = [{"dataset": "daily", "symbol": "AAA", "partition_ids": "q",
             "active_generation": "g", "min_date": "2026-01-01", "max_date": "2026-09-03",
             "row_count": 199, "status": "SUCCESS"}]
    access._read_manifest = lambda _path: pd.DataFrame(rows)
    assert runner._daily_preflight(["AAA"], access, "2026-09-03")["AAA"].status == "PREP_REQUIRED"


def test_stale_latest_session_is_separate_from_warmup(tmp_path):
    access = RoutedFixtureAccess(tmp_path)
    rows = [{"dataset": "daily", "symbol": "AAA", "partition_ids": "q",
             "active_generation": "g", "min_date": "2025-01-01", "max_date": "2026-09-02",
             "row_count": 400, "status": "SUCCESS"}]
    access._read_manifest = lambda _path: pd.DataFrame(rows)
    state = runner._daily_preflight(["AAA"], access, "2026-09-03")["AAA"]
    assert state.status == "PREP_REQUIRED"
    assert state.reason_codes == ("DAILY_STALE",)


def test_superseded_generation_does_not_inflate_warmup(tmp_path):
    access = RoutedFixtureAccess(tmp_path)
    rows = [{"dataset": "daily", "symbol": "AAA", "partition_ids": "q1",
             "active_generation": "g1", "min_date": "2025-01-01", "max_date": "2025-06-30",
             "row_count": 120, "status": "SUCCESS"},
            {"dataset": "daily", "symbol": "AAA", "partition_ids": "q1",
             "active_generation": "", "min_date": "2025-01-01", "max_date": "2025-06-30",
             "row_count": 120, "status": "SUPERSEDED"},
            {"dataset": "daily", "symbol": "AAA", "partition_ids": "q2",
             "active_generation": "g2", "min_date": "2025-07-01", "max_date": "2026-09-03",
             "row_count": 79, "status": "SUCCESS"}]
    access._read_manifest = lambda _path: pd.DataFrame(rows)
    assert runner._daily_preflight(["AAA"], access, "2026-09-03")["AAA"].status == "PREP_REQUIRED"
