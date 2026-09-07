"""Verified daily reuse must reduce I/O without accepting changed identities."""
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from pcs.data.access import PCSDataAccess, DataAccessError
from pcs.data.correctness_gate import DataCorrectnessError
from pcs.data.strategy_readiness import resolve_active_verified_daily_handle
from pcs.pool.runner import DailyReadiness, _audit_verified_daily, _daily_preflight
from pcs.pool.runtime import ManifestSnapshot, PoolRuntime


@pytest.fixture
def daily_access(tmp_path):
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    frame = pd.DataFrame({"symbol": "AAA", "date": pd.date_range("2025-01-01", periods=220),
                          "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1000})
    access.promote_generation(frame, "daily", "AAA", "year=2025", source_version="test-fixture")
    return access


def test_audit_handle_reused_and_independent_readback_kept(daily_access, monkeypatch):
    counts = {"manifest": 0, "parquet": 0, "resolver": 0}
    read_manifest = daily_access._read_manifest
    read_parquet = pd.read_parquet

    def manifest(path):
        counts["manifest"] += 1
        return read_manifest(path)

    def parquet(*args, **kwargs):
        counts["parquet"] += 1
        return read_parquet(*args, **kwargs)

    def resolver(symbol, day, warmup, *, data_access, manifest_snapshot):
        counts["resolver"] += 1
        return resolve_active_verified_daily_handle(symbol, day, warmup,
            data_access=data_access, manifest_snapshot=manifest_snapshot)

    monkeypatch.setattr(daily_access, "_read_manifest", manifest)
    monkeypatch.setattr(pd, "read_parquet", parquet)
    runtime = PoolRuntime(access=daily_access, daily_handle_resolver=resolver)
    states = _audit_verified_daily({"AAA": DailyReadiness("READY")}, ["AAA"], daily_access,
        "2025-08-08", resolver, runtime=runtime)
    assert states["AAA"].status == "READY"
    handle = runtime.resolve_daily("AAA", "2025-08-08", 200)
    frame = runtime.read_daily(handle, required_warmup_rows=200)
    frame.loc[0, "close"] = 9
    assert runtime.read_daily(handle, required_warmup_rows=200).loc[0, "close"] == 100
    assert counts == {"manifest": 1, "parquet": 2, "resolver": 1}
    with pytest.raises(DataCorrectnessError, match="DATASET_CHECKSUM_MISMATCH"):
        runtime.read_daily(replace(handle, checksum="wrong"), required_warmup_rows=200)


def test_manifest_generation_change_invalidates_cached_handle(daily_access):
    runtime = PoolRuntime(access=daily_access, daily_handle_resolver=resolve_active_verified_daily_handle)
    old = runtime.resolve_daily("AAA", "2025-08-08", 200)
    frame = daily_access.read_verified_dataset(old).assign(close=100.5)
    daily_access.promote_generation(frame, "daily", "AAA", "year=2025", source_version="new-fixture")
    with pytest.raises(ValueError, match="MANIFEST_SNAPSHOT_CHANGED"):
        runtime.resolve_daily("AAA", "2025-08-08", 200)
    runtime.refresh_manifest_snapshot()
    new = runtime.resolve_daily("AAA", "2025-08-08", 200)
    assert new.generation_id != old.generation_id


def test_foreign_manifest_snapshot_cannot_authorize_partition(daily_access, tmp_path):
    other = PCSDataAccess.isolated(manifest_path=tmp_path / "other.csv", parquet_root=tmp_path / "other")
    wrong = ManifestSnapshot.capture(other)
    handle = resolve_active_verified_daily_handle("AAA", "2025-08-08", 200,
        data_access=daily_access, manifest_snapshot=wrong)
    assert len(daily_access.read_verified_dataset(handle, manifest_snapshot=wrong)) == 220


def test_mutated_partition_invalidates_frame_cache(daily_access):
    runtime = PoolRuntime(access=daily_access, daily_handle_resolver=resolve_active_verified_daily_handle)
    handle = runtime.resolve_daily("AAA", "2025-08-08", 200)
    runtime.read_daily(handle)
    path = Path(handle.canonical_paths[0])
    pd.read_parquet(path).assign(close=100.5).to_parquet(path, index=False)
    with pytest.raises(DataAccessError, match="READ_BACK_CHECKSUM_MISMATCH"):
        runtime.read_daily(handle)


def test_stricter_warmup_is_not_bypassed_by_frame_cache(daily_access):
    runtime = PoolRuntime(access=daily_access, daily_handle_resolver=resolve_active_verified_daily_handle)
    handle = runtime.resolve_daily("AAA", "2025-08-08", 200)
    runtime.read_daily(handle, required_warmup_rows=200)
    with pytest.raises(DataCorrectnessError, match="INSUFFICIENT_FEATURE_WARMUP"):
        runtime.read_daily(handle, required_warmup_rows=221)


def test_absent_symbol_reason_unchanged_with_snapshot(daily_access):
    plain = _daily_preflight(["ABSENT"], daily_access, "2025-08-08")
    cached = _daily_preflight(["ABSENT"], daily_access, "2025-08-08",
                              manifest_snapshot=ManifestSnapshot.capture(daily_access))
    assert plain == cached
