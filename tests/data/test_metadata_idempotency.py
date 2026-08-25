from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import pytest

from pcs.data.access import PCSDataAccess, DataQualityError


def _access(tmp_path):
    return PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet", source_routes={})


def test_manifest_duplicate_and_conflict(tmp_path):
    a = _access(tmp_path)
    frame = pd.DataFrame({"date": ["2024-01-02"]})
    a.update_manifest("daily", "AAA", frame, "aaa.parquet", "v1", "year=2024/quarter=1")
    a.update_manifest("daily", "AAA", frame, "aaa.parquet", "v1", "year=2024/quarter=1")
    assert len(pd.read_csv(tmp_path / "manifest.csv")) == 1
    with pytest.raises(DataQualityError):
        a.update_manifest("daily", "AAA", frame.assign(date=["2024-01-03"]), "other.parquet", "v2", "year=2024/quarter=1")


def test_provenance_duplicate_and_conflict(tmp_path):
    a = _access(tmp_path)
    path = tmp_path / "prov.csv"
    record = {"dataset": "daily", "symbol": "AAA", "year": 2024, "source_version": "v1", "row_count": 1}
    a.record_provenance(record, path)
    a.record_provenance(record, path)
    assert len(pd.read_csv(path)) == 1
    with pytest.raises(DataQualityError):
        a.record_provenance({**record, "row_count": 2}, path)


def test_concurrent_identical_metadata_writes(tmp_path):
    def write(_):
        _access(tmp_path).record_provenance({"dataset": "daily", "symbol": "AAA", "year": 2024, "source_version": "v1"}, tmp_path / "prov.csv")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))
    assert len(pd.read_csv(tmp_path / "prov.csv")) == 1


def test_semantic_hash_ignores_run_metadata(tmp_path):
    a = _access(tmp_path)
    x = pd.DataFrame({"value": [1], "created_at": ["2024-01-01"], "run_id": ["a"]})
    y = x.assign(created_at="2025-01-01", run_id="b")
    assert a.semantic_content_hash(x) == a.semantic_content_hash(y)
    p = a.write_artifact(x, "test", "result", root=tmp_path)
    assert a.write_artifact(y, "test", "result", root=tmp_path) == p


def test_empty_artifact_writes_sidecar_and_repairs_interrupted_retry(tmp_path):
    a = _access(tmp_path)
    empty = pd.DataFrame({"value": pd.Series(dtype="int64")})

    path = a.write_artifact(empty, "test", "empty", root=tmp_path)
    sidecar = path.with_suffix(path.suffix + ".semantic.json")
    assert path.exists()
    assert sidecar.exists()
    assert pd.read_parquet(path).empty

    sidecar.unlink()
    assert a.write_artifact(empty, "test", "empty", root=tmp_path) == path
    assert sidecar.exists()


def test_concurrent_manifest_updates_and_interruption(tmp_path, monkeypatch):
    def write(partition):
        _access(tmp_path).update_manifest("daily", "AAA", pd.DataFrame({"date": [f"2024-0{partition}-02"]}), f"p{partition}.parquet", "v1", f"year=2024/quarter={partition}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(1, 3)))
    manifest = tmp_path / "manifest.csv"
    assert len(pd.read_csv(manifest)) == 2
    before = manifest.read_bytes()
    original_replace = __import__("pcs.data.access", fromlist=["os"]).os.replace
    def fail_once(src, dst):
        if str(dst).endswith("manifest.csv"):
            raise OSError("simulated interruption")
        return original_replace(src, dst)
    monkeypatch.setattr("pcs.data.access.os.replace", fail_once)
    with pytest.raises(OSError):
        _access(tmp_path).update_manifest("daily", "AAA", pd.DataFrame({"date": ["2024-03-02"]}), "p3.parquet", "v1", "year=2024/quarter=3")
    assert manifest.read_bytes() == before
