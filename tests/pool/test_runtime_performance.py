from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import time

import pandas as pd

from pcs.pool.runner import run_pcs_pool
from pcs.pool.runtime import ManifestSnapshot, PoolRuntime


class Access:
    manifest_path = "manifest.csv"

    def __init__(self):
        self.manifest_reads = 0
        self.frame_reads = 0

    def _read_manifest(self, _path):
        self.manifest_reads += 1
        return pd.DataFrame([{"dataset": "daily", "symbol": "AAA", "generation": "g1"}])

    def read_verified_dataset(self, _handle, **_kwargs):
        self.frame_reads += 1
        return pd.DataFrame({"date": pd.date_range("2025-01-01", periods=3), "close": [1, 2, 3]})


def handle(ticker="AAA"):
    return SimpleNamespace(dataset="daily", ticker=ticker, generation_id="g1",
                           checksum="c1", dataset_fingerprint="f1")


def test_manifest_is_captured_once_and_daily_handle_is_single_flight():
    access = Access()
    runtime = PoolRuntime(access=access, run_id="run", as_of="2025-01-03")
    calls = {"resolver": 0}

    def resolver(symbol, as_of, warmup, *, data_access, manifest_snapshot):
        calls["resolver"] += 1
        assert manifest_snapshot.identity == runtime.manifest_snapshot_id
        return handle(symbol)

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(
            lambda _: runtime.resolve_daily_handle("AAA", "2025-01-03", 200, resolver=resolver),
            range(8),
        ))
    assert access.manifest_reads == 1
    assert calls["resolver"] == 1
    assert all(value is values[0] for value in values)
    assert isinstance(runtime.manifest_snapshot, ManifestSnapshot)


def test_daily_frame_is_read_once_and_callers_get_defensive_copies():
    access = Access()
    runtime = PoolRuntime(access=access)
    first = runtime.read_daily(handle(), end_date="2025-01-02")
    first.loc[0, "close"] = 999
    second = runtime.read_daily(handle(), end_date="2025-01-03")
    assert access.frame_reads == 1
    assert second.loc[0, "close"] == 1
    assert len(first) == 2
    assert len(second) == 3


def test_daily_handle_cache_does_not_reuse_other_decision_session():
    calls = []
    def resolver(symbol, as_of, warmup, *, data_access):
        calls.append((symbol, as_of, warmup))
        return handle(symbol)

    runtime = PoolRuntime(daily_handle_resolver=resolver)
    runtime.resolve_daily("aaa", "2025-01-02", 200)
    runtime.resolve_daily("AAA", "2025-01-02", 200)
    runtime.resolve_daily("AAA", "2025-01-03", 200)
    assert calls == [("AAA", "2025-01-02", 200), ("AAA", "2025-01-03", 200)]


def test_stage_timeout_is_bounded_and_ordered():
    runtime = PoolRuntime(stage_timeout_seconds=0.01)
    started = time.perf_counter()
    result = runtime.run_stage(["SLOW", "FAST"],
                               lambda symbol: time.sleep(0.2) if symbol == "SLOW" else symbol,
                               stage_name="daily_timing")
    elapsed = time.perf_counter() - started
    assert elapsed < 0.1
    assert [item.symbol for item in result.outcomes] == ["SLOW", "FAST"]
    assert result.outcomes[0].reason_codes == ("WORKER_TIMEOUT",)
    assert result.elapsed_ms < 100
    assert runtime.stage_latency_ms["daily_timing"] == result.elapsed_ms


def test_100_ticker_smoke_is_replayable_without_duplicate_reads():
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=220),
        "open": range(220), "high": range(1, 221),
        "low": range(220), "close": range(1, 221),
        "volume": [1000] * 220,
    })
    symbols = [f"TICKER{i:03d}" for i in range(100)]
    access = Access()
    access.frames = {symbol: frame for symbol in ["QQQ", *symbols]}
    access.read_verified_dataset = lambda handle, **_kwargs: (
        setattr(access, "frame_reads", access.frame_reads + 1) or
        access.frames[handle.ticker].copy()
    )

    def resolver(symbol, _as_of, _warmup, *, data_access):
        return SimpleNamespace(ticker=symbol)

    first = run_pcs_pool(symbols=symbols, as_of="2025-08-08", mode="EOD",
                         data_access=access, daily_handle_resolver=resolver,
                         max_workers=8)
    second = run_pcs_pool(symbols=symbols, as_of="2025-08-08", mode="EOD",
                          data_access=access, daily_handle_resolver=resolver,
                          max_workers=8)
    assert len(first.ticker_results) == len(second.ticker_results) == 100
    assert first.summary == second.summary
    assert [(row.symbol, row.final_action) for row in first.ticker_results] == \
           [(row.symbol, row.final_action) for row in second.ticker_results]
    assert first.counters["daily_frame_reads"] == 101  # 100 tickers + QQQ
    assert second.counters["daily_frame_reads"] == 101
    assert first.counters["handle_resolution_calls"] == 101
    assert second.counters["handle_resolution_calls"] == 101
    assert access.frame_reads == 202
