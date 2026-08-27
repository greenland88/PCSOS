import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.historical_correction import correct_partitions


def frame(close=10.0):
    return pd.DataFrame({"symbol": ["TEST"], "date": ["2024-01-02"], "open": [close], "high": [close + 1], "low": [close - 1], "close": [close], "volume": [100]})


def test_correction_changes_only_declared_partition(tmp_path):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    access.write_partition(frame(), "daily", "TEST", "year=2024", source_version="source-a")
    result = correct_partitions("TEST", "daily", frame(11), affected_partitions=["year=2024"], source_version="source-b", correction_reason="authoritative vendor correction", access=access)
    assert result.status == "COMPLETED"
    assert result.POST_CORRECTION_READY == "YES"
    assert result.UNEXPECTED_CHANGED_PARTITIONS == []
    assert float(pd.read_parquet(tmp_path / "parquet" / "daily" / "symbol=TEST" / "year=2024" / "TEST_year_2024.parquet").close.iloc[0]) == 11


def test_failed_post_write_correction_rolls_back_files_and_manifest(tmp_path, monkeypatch):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    first = frame(10)
    second = frame(20).assign(date="2025-01-02")
    access.write_partition(first, "daily", "TEST", "year=2024", source_version="source-a")
    access.write_partition(second, "daily", "TEST", "year=2025", source_version="source-a")
    before = {p: p.read_bytes() for p in (tmp_path / "parquet" / "daily" / "symbol=TEST" / "year=2024" / "TEST_year_2024.parquet", tmp_path / "parquet" / "daily" / "symbol=TEST" / "year=2025" / "TEST_year_2025.parquet")}
    manifest_before = (tmp_path / "manifest.csv").read_bytes()
    monkeypatch.setattr(access, "update_manifest", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced post-write failure")))
    result = correct_partitions("TEST", "daily", pd.concat([first.assign(close=11), second.assign(close=21)]), affected_partitions=["year=2024", "year=2025"], source_version="source-b", correction_reason="rollback acceptance", access=access)
    assert result.ROLLBACK_REQUIRED == "YES" and result.rollback_verified is True
    assert {p: p.read_bytes() for p in before} == before
    assert (tmp_path / "manifest.csv").read_bytes() == manifest_before
