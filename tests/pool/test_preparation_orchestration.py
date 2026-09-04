from types import SimpleNamespace

import pandas as pd

import pcs.pool.runner as runner
from pcs.pool.models import EligibilityStatus
from pcs.pool.runtime import ManifestSnapshot


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
