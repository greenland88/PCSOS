import hashlib
from pathlib import Path

import pandas as pd
import pytest

from pcs.data.access import PCSDataAccess
from pcs.data.strategy_readiness import resolve_active_verified_daily_handle


def _daily_frame(start, periods, symbol="AAA"):
    dates = pd.date_range(start, periods=periods, freq="B")
    close = pd.Series(range(periods), dtype=float) + 100
    return pd.DataFrame({"symbol": symbol, "date": dates, "open": close,
                         "high": close + 1, "low": close - 1,
                         "close": close, "volume": 1000})


def _write_active(access, frame, year, generation, *, content_hash=None):
    path = access.parquet_root / "daily" / "symbol=AAA" / f"year={year}" / f"{generation}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return {"dataset": "daily", "symbol": "AAA", "year": year, "quarter": None,
            "parquet_path": str(path), "active_generation": generation,
            "previous_generation": None, "partition_ids": f"year={year}",
            "row_count": len(frame), "min_date": str(frame.date.min().date()),
            "max_date": str(frame.date.max().date()), "schema_version": "2",
            "content_hash": content_hash or access.semantic_content_hash(frame),
            "status": "SUCCESS"}


def test_daily_resolver_pins_minimal_suffix_and_skips_ancient_corruption(tmp_path):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    old = _daily_frame("2010-01-01", 250)
    current = _daily_frame("2025-01-01", 250)
    rows = [_write_active(access, old, 2010, "old-identity", content_hash="wrong-old-hash"),
            _write_active(access, current, 2025, access.semantic_content_hash(current))]
    pd.DataFrame(rows).to_csv(access.manifest_path, index=False)
    handle = resolve_active_verified_daily_handle("AAA", "2025-12-16", 200, data_access=access)
    assert handle.partition_count == 1
    assert handle.partitions == ("year=2025",)
    assert handle.row_count == 250


def test_daily_resolver_blocks_required_window_checksum_mismatch(tmp_path):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    frame = _daily_frame("2025-01-01", 250)
    rows = [_write_active(access, frame, 2025, "required-identity", content_hash="wrong-hash")]
    pd.DataFrame(rows).to_csv(access.manifest_path, index=False)
    with pytest.raises(Exception, match="READ_BACK_CHECKSUM_MISMATCH"):
        resolve_active_verified_daily_handle("AAA", "2025-12-16", 200, data_access=access)
