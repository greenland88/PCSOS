import pandas as pd
import pytest
from pcs.data.storage_schema import OPTION_FIELDS

from pcs.data.access import PCSDataAccess, DataQualityError


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
