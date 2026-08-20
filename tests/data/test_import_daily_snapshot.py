import pandas as pd

from pcs.data.import_daily_snapshot import find_daily_snapshots, find_latest_daily_snapshot, import_daily_snapshot, main


def test_import_daily_snapshot_appends_and_deduplicates(tmp_path):
    source = tmp_path / "daily_2026-08-17.csv"
    root = tmp_path / "hist"
    root.mkdir()
    pd.DataFrame(
        {
            "日期": ["2026-08-14", "2026-08-17"],
            "代码": ["NVDA", "NVDA"],
            "开盘价": [1, 2],
            "收盘价": [1.5, 2.5],
            "最高价": [2, 3],
            "最低价": [0.5, 1.5],
            "成交量": [10, 20],
        }
    ).to_csv(root / "NVDA_daily_qfq.csv", index=False)
    pd.DataFrame(
        {
            "日期": ["2026-08-17", "2026-08-18", "2026-08-18"],
            "代码": ["NVDA", "NVDA", "QQQ"],
            "开盘价": [4, 5, 10],
            "收盘价": [4.5, 5.5, 10.5],
            "最高价": [5, 6, 11],
            "最低价": [3.5, 4.5, 9.5],
            "成交量": [40, 50, 100],
            "成交额": [1, 1, 1],
        }
    ).to_csv(source, index=False)

    result = import_daily_snapshot(source, root, run_id="run_test", request_id="req_test")

    assert result.status == "SUCCESS"
    assert result.symbols_written == 2
    assert result.files_updated == 1
    assert result.files_created == 1
    nvda = pd.read_csv(root / "NVDA_daily_qfq.csv")
    assert list(nvda["日期"]) == ["2026-08-14", "2026-08-17", "2026-08-18"]
    assert nvda.loc[nvda["日期"] == "2026-08-17", "开盘价"].iloc[0] == 4
    qqq = pd.read_csv(root / "QQQ_daily_qfq.csv")
    assert len(qqq) == 1


def test_import_daily_snapshot_rejects_invalid_source_without_writes(tmp_path):
    source = tmp_path / "daily_2026-08-17.csv"
    root = tmp_path / "hist"
    root.mkdir()
    existing = root / "NVDA_daily_qfq.csv"
    pd.DataFrame(
        {
            "日期": ["2026-08-14"],
            "代码": ["NVDA"],
            "开盘价": [1],
            "收盘价": [1.5],
            "最高价": [2],
            "最低价": [0.5],
            "成交量": [10],
        }
    ).to_csv(existing, index=False)
    before = existing.read_text(encoding="utf-8")
    pd.DataFrame(
        {
            "日期": ["2026-08-17"],
            "代码": ["NVDA"],
            "开盘价": [4],
            "收盘价": [4.5],
            "最高价": [3],
            "最低价": [3.5],
            "成交量": [40],
        }
    ).to_csv(source, index=False)

    result = import_daily_snapshot(source, root)

    assert result.status == "FAILED"
    assert result.reason_codes == ["INVALID_DAILY_DATA"]
    assert existing.read_text(encoding="utf-8") == before


def test_import_daily_snapshot_can_explicitly_skip_invalid_rows(tmp_path):
    source = tmp_path / "daily_2026-08-17.csv"
    root = tmp_path / "hist"
    pd.DataFrame(
        {
            "日期": ["2026-08-17", "2026-08-17"],
            "代码": ["NVDA", "RANGU"],
            "开盘价": [4, 10.85],
            "收盘价": [4.5, 11.32],
            "最高价": [5, 10.85],
            "最低价": [3.5, 11.32],
            "成交量": [40, 0],
        }
    ).to_csv(source, index=False)

    result = import_daily_snapshot(source, root, skip_invalid_rows=True)

    assert result.status == "SUCCESS"
    assert result.reason_codes == ["SKIPPED_INVALID_ROWS"]
    assert result.skipped_invalid_rows == 1
    assert result.skipped_invalid_symbols == ["RANGU"]
    assert (root / "NVDA_daily_qfq.csv").exists()
    assert not (root / "RANGU_daily_qfq.csv").exists()


def test_import_daily_snapshot_does_not_repair_existing_history(tmp_path):
    source = tmp_path / "daily_2026-08-17.csv"
    root = tmp_path / "hist"
    root.mkdir()
    pd.DataFrame(
        {
            "日期": ["2026-08-14"],
            "代码": ["AACG"],
            "开盘价": [-2.66],
            "收盘价": [-2.85],
            "最高价": [-2.81],
            "最低价": [-2.85],
            "成交量": [1095],
        }
    ).to_csv(root / "AACG_daily_qfq.csv", index=False)
    pd.DataFrame(
        {
            "日期": ["2026-08-17"],
            "代码": ["AACG"],
            "开盘价": [1],
            "收盘价": [1.5],
            "最高价": [2],
            "最低价": [0.5],
            "成交量": [10],
        }
    ).to_csv(source, index=False)

    result = import_daily_snapshot(source, root)

    assert result.status == "SUCCESS"
    out = pd.read_csv(root / "AACG_daily_qfq.csv")
    assert list(out["日期"]) == ["2026-08-14", "2026-08-17"]


def test_import_daily_snapshot_can_sync_parquet(tmp_path):
    source = tmp_path / "daily_2026-08-17.csv"
    root = tmp_path / "hist"
    parquet_root = tmp_path / "parquet" / "daily"
    pd.DataFrame(
        {
            "日期": ["2026-08-17"],
            "代码": ["NVDA"],
            "开盘价": [4],
            "收盘价": [4.5],
            "最高价": [5],
            "最低价": [3.5],
            "成交量": [40],
        }
    ).to_csv(source, index=False)

    result = import_daily_snapshot(source, root, sync_parquet=True, parquet_root=parquet_root)

    assert result.status == "SUCCESS"
    assert result.parquet_synced is True
    assert result.parquet_symbols_synced == 1
    assert result.parquet_partitions_written == 1
    out = pd.read_parquet(parquet_root / "symbol=NVDA" / "year=2026" / "NVDA_2026.parquet")
    assert out.loc[0, "symbol"] == "NVDA"
    assert out.loc[0, "close"] == 4.5


def test_import_daily_snapshot_skips_parquet_when_csv_unchanged(tmp_path):
    source = tmp_path / "daily_2026-08-17.csv"
    root = tmp_path / "hist"
    parquet_root = tmp_path / "parquet" / "daily"
    rows = pd.DataFrame(
        {
            "日期": ["2026-08-17"],
            "代码": ["NVDA"],
            "开盘价": [4],
            "收盘价": [4.5],
            "最高价": [5],
            "最低价": [3.5],
            "成交量": [40],
        }
    )
    rows.to_csv(source, index=False)
    import_daily_snapshot(source, root, sync_parquet=True, parquet_root=parquet_root)

    result = import_daily_snapshot(source, root, sync_parquet=True, parquet_root=parquet_root)

    assert result.status == "SUCCESS"
    assert result.rows_written == 0
    assert result.parquet_symbols_synced == 0


def test_import_daily_snapshot_defaults_to_latest_daily_file(tmp_path):
    daily_root = tmp_path / "daily"
    root = tmp_path / "hist"
    daily_root.mkdir()
    older = daily_root / "daily_2026-08-17.csv"
    newer = daily_root / "daily_2026-08-18.csv"
    pd.DataFrame(
        {
            "日期": ["2026-08-17"],
            "代码": ["NVDA"],
            "开盘价": [4],
            "收盘价": [4.5],
            "最高价": [5],
            "最低价": [3.5],
            "成交量": [40],
        }
    ).to_csv(older, index=False)
    pd.DataFrame(
        {
            "日期": ["2026-08-18"],
            "代码": ["QQQ"],
            "开盘价": [10],
            "收盘价": [10.5],
            "最高价": [11],
            "最低价": [9.5],
            "成交量": [100],
        }
    ).to_csv(newer, index=False)

    result = import_daily_snapshot(daily_root=daily_root, historical_root=root)

    assert find_latest_daily_snapshot(daily_root) == newer
    assert result.source_path == str(newer)
    assert result.as_of == "2026-08-18"
    assert (root / "QQQ_daily_qfq.csv").exists()


def test_main_imports_all_daily_files_by_default(tmp_path):
    daily_root = tmp_path / "daily"
    root = tmp_path / "hist"
    parquet_root = tmp_path / "parquet" / "daily"
    daily_root.mkdir()
    for date_text, symbol in [("2026-08-17", "NVDA"), ("2026-08-18", "QQQ")]:
        pd.DataFrame(
            {
                "日期": [date_text],
                "代码": [symbol],
                "开盘价": [4],
                "收盘价": [4.5],
                "最高价": [5],
                "最低价": [3.5],
                "成交量": [40],
            }
        ).to_csv(daily_root / f"daily_{date_text}.csv", index=False)

    code = main(["--daily-root", str(daily_root), "--historical-root", str(root), "--parquet-root", str(parquet_root)])

    assert code == 0
    assert find_daily_snapshots(daily_root) == [daily_root / "daily_2026-08-17.csv", daily_root / "daily_2026-08-18.csv"]
    assert (root / "NVDA_daily_qfq.csv").exists()
    assert (root / "QQQ_daily_qfq.csv").exists()
    assert (parquet_root / "symbol=NVDA" / "year=2026" / "NVDA_2026.parquet").exists()


def test_main_skips_unchanged_files_from_manifest(tmp_path, capsys):
    daily_root = tmp_path / "daily"
    root = tmp_path / "hist"
    parquet_root = tmp_path / "parquet" / "daily"
    manifest = tmp_path / "manifest.csv"
    daily_root.mkdir()
    pd.DataFrame(
        {
            "日期": ["2026-08-17"],
            "代码": ["NVDA"],
            "开盘价": [4],
            "收盘价": [4.5],
            "最高价": [5],
            "最低价": [3.5],
            "成交量": [40],
        }
    ).to_csv(daily_root / "daily_2026-08-17.csv", index=False)

    first = main(["--daily-root", str(daily_root), "--historical-root", str(root), "--parquet-root", str(parquet_root), "--manifest", str(manifest)])
    capsys.readouterr()
    second = main(["--daily-root", str(daily_root), "--historical-root", str(root), "--parquet-root", str(parquet_root), "--manifest", str(manifest)])
    out = capsys.readouterr().out

    assert first == 0
    assert second == 0
    assert "'files_processed': 0" in out
    assert "'files_skipped': 1" in out


def test_main_records_daily_source_provenance_with_manifest_link(tmp_path):
    from pcs.data.import_daily_snapshot import main

    source = tmp_path / "daily_2026-08-03.csv"
    pd.DataFrame([{"日期": "2026-08-03", "代码": "ZZZ", "开盘价": 10, "最高价": 11,
                   "最低价": 9, "收盘价": 10.5, "成交量": 100}]).to_csv(source, index=False, encoding="utf-8-sig")
    manifest = tmp_path / "manifest.csv"
    provenance = tmp_path / "provenance.csv"
    assert main(["--file", str(source), "--historical-root", str(tmp_path / "history"),
                 "--no-sync-parquet", "--manifest", str(manifest), "--provenance", str(provenance)]) == 0

    m = pd.read_csv(manifest).iloc[0]
    p = pd.read_csv(provenance).iloc[0]
    assert p["source"] == "daily_snapshot_csv"
    assert p["source_table"] == "daily_snapshot"
    assert p["source_path"] == str(source)
    assert p["query_start"] == "2026-08-03"
    assert p["query_end"] == "2026-08-03"
    assert int(p["rows_written"]) == 1
    assert len(str(p["sha256"])) == 64
    assert p["sha256"] == m["sha256"]
    assert p["manifest_path"] == str(manifest)
