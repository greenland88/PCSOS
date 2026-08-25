import pandas as pd
import pytest
from pcs.data.storage_schema import OPTION_FIELDS

from pcs.data.access import PCSDataAccess, DataQualityError


def test_data_root_is_explicit_and_does_not_depend_on_current_directory(tmp_path, monkeypatch):
    data_root = tmp_path / "canonical-data"
    (data_root / "manifests").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "other" if (tmp_path / "other").exists() else tmp_path)

    access = PCSDataAccess(data_root=data_root)

    assert access.data_root == data_root
    assert access.manifest_path == data_root / "manifests" / "storage_manifest.csv"
    assert access.parquet_root == data_root / "parquet"


def test_data_root_can_be_configured_without_changing_callers(tmp_path, monkeypatch):
    data_root = tmp_path / "configured-data"
    monkeypatch.setenv("PCS_CANONICAL_DATA_ROOT", str(data_root))

    access = PCSDataAccess()

    assert access.data_root == data_root
    assert access.manifest_path == data_root / "manifests" / "storage_manifest.csv"


def test_relative_configured_routes_follow_data_root(tmp_path):
    data_root = tmp_path / "canonical-data"
    access = PCSDataAccess(
        data_root=data_root,
        source_routes={"options": {"by_symbol": {
            "ZZZ": {"dataset": "options_v2",
                    "manifest_path": "data/manifests/options_v2.csv",
                    "parquet_root": "data/parquet"}
        }}},
    )

    _, manifest, parquet = access._resolve_route("options", "ZZZ")

    assert manifest == data_root / "manifests" / "options_v2.csv"
    assert parquet == data_root / "parquet"


def test_routed_source_identity_uses_resolved_dataset_rows_only(tmp_path):
    data_root = tmp_path / "canonical-data"
    manifest_dir = data_root / "manifests"
    option_dir = data_root / "parquet" / "options_v2" / "symbol=ZZZ" / "year=2026" / "quarter=1"
    manifest_dir.mkdir(parents=True)
    option_dir.mkdir(parents=True)
    pd.DataFrame({"symbol": ["ZZZ"], "trade_date": ["2026-01-02"], "expiration_date": ["2026-02-20"],
                  "strike": [100.0], "call_put": ["p"], "bid": [1.0]}).to_parquet(option_dir / "quotes.parquet", index=False)
    manifest = pd.DataFrame([
        {"dataset": "options_v2", "symbol": "ZZZ", "status": "SUCCESS", "row_count": 1,
         "min_date": "2026-01-02", "max_date": "2026-01-02", "schema_version": "1"},
    ])
    manifest.to_csv(manifest_dir / "storage_manifest.csv", index=False)
    routes = {"options": {"by_symbol": {"ZZZ": {"dataset": "options_v2"}}}}
    first = PCSDataAccess(data_root=data_root, source_routes=routes).source_data_identity("options", "ZZZ")
    manifest.loc[len(manifest)] = {"dataset": "daily", "symbol": "ZZZ", "status": "SUCCESS", "row_count": 99,
                                   "min_date": "2020-01-01", "max_date": "2026-01-02", "schema_version": "1"}
    manifest.to_csv(manifest_dir / "storage_manifest.csv", index=False)
    second = PCSDataAccess(data_root=data_root, source_routes=routes).source_data_identity("options", "ZZZ")
    assert first == second


def test_source_identity_recursive_partitions_is_cwd_independent(tmp_path, monkeypatch):
    data_root = tmp_path / "canonical-data"
    manifest_dir = data_root / "manifests"
    first = data_root / "parquet" / "options_v2" / "symbol=ZZZ" / "year=2026" / "quarter=1"
    second = data_root / "parquet" / "options_v2" / "symbol=ZZZ" / "year=2026" / "quarter=2"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    first_file = first / "q1.parquet"
    second_file = second / "q2.parquet"
    pd.DataFrame({"symbol": ["ZZZ"], "trade_date": ["2026-01-02"]}).to_parquet(first_file, index=False)
    pd.DataFrame({"symbol": ["ZZZ"], "trade_date": ["2026-04-01"]}).to_parquet(second_file, index=False)
    manifest_dir.mkdir(parents=True)
    pd.DataFrame([{"dataset": "options_v2", "symbol": "ZZZ", "status": "SUCCESS",
                   "row_count": 2, "min_date": "2026-01-02", "max_date": "2026-04-01",
                   "schema_version": "1"}]).to_csv(manifest_dir / "storage_manifest.csv", index=False)
    access = PCSDataAccess(data_root=data_root, source_routes={"options": {"by_symbol": {"ZZZ": {"dataset": "options_v2"}}}})
    original = access.source_data_identity("options", "ZZZ")
    monkeypatch.chdir(tmp_path)
    changed_cwd = access.source_data_identity("options", "ZZZ")
    assert original == changed_cwd


def _options(rows):
    return pd.DataFrame(rows, columns=["symbol", "trade_date", "expiration_date", "strike", "call_put", "last", "bid", "ask", "bid_iv", "ask_iv", "open_interest", "volume", "delta", "gamma", "vega", "theta", "rho"])


def test_write_rejects_conflicting_contract_keys(tmp_path):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    frame = _options([
        ["ZZZ", "2026-01-02", "2026-02-20", 100.25, "p", 1, .9, 1.1, None, None, 1, 1, None, None, None, None, None],
        ["ZZZ", "2026-01-02", "2026-02-20", 100.25, "p", 1.2, 1, 1.3, None, None, 1, 1, None, None, None, None, None],
    ])
    with pytest.raises(DataQualityError, match="ambiguous"):
        access.write_partition(frame, "options", "ZZZ", "year=2026/quarter=1", source_version="test")


def test_write_preserves_fractional_strike_and_is_atomic(tmp_path):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    frame = _options([["ZZZ", "2026-01-02", "2026-02-20", 100.25, "p", 1, .9, 1.1, None, None, 1, 1, None, None, None, None, None]])
    path = access.write_partition(frame, "options", "ZZZ", "year=2026/quarter=1", source_version="raw-v1")
    assert path.exists()
    loaded = access.read_quotes("ZZZ", "2026-01-02", "2026-01-02")
    assert loaded.strike.tolist() == [100.25]
    assert access.get_provenance("options", "ZZZ")[0]["source_file"] == "raw-v1"
    with pytest.raises(FileExistsError):
        access.write_partition(frame, "options", "ZZZ", "year=2026/quarter=1", source_version="raw-v1")


def test_read_enforces_ticker_and_coverage(tmp_path):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    frame = _options([["ZZZ", "2026-01-02", "2026-02-20", 100.25, "p", 1, .9, 1.1, None, None, 1, 1, None, None, None, None, None]])
    access.write_partition(frame, "options", "ZZZ", "year=2026/quarter=1", source_version="raw-v1")
    with pytest.raises(ValueError): access.read_quotes("ZZZ", "2025-01-01", "2026-01-02")
    with pytest.raises(FileNotFoundError): access.read_quotes("QQQ", "2026-01-02", "2026-01-02")


def test_partition_replace_updates_manifest_and_provenance_atomically(tmp_path):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    frame = _options([["ZZZ", "2026-08-03", "2026-09-18", 100, "p", 1, .9, 1.1, None, None, 1, 1, None, None, None, None, None]])
    path = access.write_partition(
        frame, "options_v2", "ZZZ", "year=2026/quarter=3", source_version="clickhouse:v1",
        filename="ZZZ_2026_q3.parquet", replace_manifest=True,
    )
    access.record_provenance({"source": "ClickHouse", "rows_written": 1}, tmp_path / "provenance.csv")
    assert path.exists()
    assert len(access.get_provenance("options_v2", "ZZZ")) == 1
    assert pd.read_csv(tmp_path / "provenance.csv").iloc[0]["source"] == "ClickHouse"


def test_partition_write_rolls_back_parquet_when_manifest_commit_fails(tmp_path, monkeypatch):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    frame = _options([["ZZZ", "2026-08-03", "2026-09-18", 100, "p", 1, .9, 1.1, None, None, 1, 1, None, None, None, None, None]])

    def fail_manifest(*args, **kwargs):
        raise OSError("manifest commit failed")

    monkeypatch.setattr(access, "update_manifest", fail_manifest)
    with pytest.raises(OSError, match="manifest commit failed"):
        access.write_partition(frame, "options", "ZZZ", "year=2026/quarter=3", source_version="v1")
    assert not list((tmp_path / "parquet").rglob("*.parquet"))
    assert not (tmp_path / "manifest.csv").exists()


def test_clickhouse_incremental_sync_records_complete_provenance_and_manifest(tmp_path, monkeypatch):
    """A successful increment must never leave data without an auditable origin."""
    from scripts import sync_clickhouse_options as sync

    source_frame = _options([["ZZZ", "2026-08-02", "2026-09-18", 100.25, "p", 1, .9, 1.1,
                              None, None, 1000, 200, None, None, None, None, None]])

    def fake_query(_url, _user, _password, _sql, path):
        source_frame[OPTION_FIELDS].to_parquet(path, index=False)

    monkeypatch.setattr(sync, "_query", fake_query)
    manifest_path = tmp_path / "storage_manifest.csv"
    provenance_path = tmp_path / "data_provenance_manifest.csv"
    root = tmp_path / "parquet" / "options_v2"
    monkeypatch.setattr("sys.argv", [
        "sync_clickhouse_options.py", "--password", "test", "--symbol", "ZZZ",
        "--start", "2026-08-01", "--end", "2026-08-03", "--apply",
        "--root", str(root), "--manifest-path", str(manifest_path),
        "--provenance-path", str(provenance_path),
    ])

    sync.main()

    manifest = pd.read_csv(manifest_path)
    provenance = pd.read_csv(provenance_path)
    assert len(manifest) == len(provenance) == 1
    m, p = manifest.iloc[0], provenance.iloc[0]

    required_provenance = {
        "source", "source_table", "symbol", "query_start", "query_end",
        "rows_written", "checksum_sha256", "source_version", "dataset", "parquet_path",
    }
    assert required_provenance.issubset(provenance.columns)
    assert p["source"] == "ClickHouse"
    assert p["source_table"] == sync.SOURCE
    assert p["symbol"] == "ZZZ"
    assert p["query_start"] == "2026-08-01"
    assert p["query_end"] == "2026-08-03"
    assert int(p["rows_written"]) == 1
    assert len(str(p["checksum_sha256"])) == 64
    assert str(p["source_version"]).endswith(str(p["checksum_sha256"]))

    assert m["dataset"] == "options_v2"
    assert m["symbol"] == "ZZZ"
    assert int(m["row_count"]) == int(p["rows_written"])
    assert m["min_date"] == "2026-08-02"
    assert m["max_date"] == "2026-08-02"
    assert m["source_file"] == p["source_version"]
    assert m["parquet_path"] == p["parquet_path"]
    assert str(m["source_file"]).startswith("clickhouse:")
