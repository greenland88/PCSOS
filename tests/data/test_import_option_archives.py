import zipfile

import pandas as pd

from pcs.data.import_option_archives import import_option_archives


def test_import_option_archives_extracts_symbols_and_skips_manifest(tmp_path):
    archive_root = tmp_path / "archives"
    raw_root = tmp_path / "raw" / "options"
    parquet_root = tmp_path / "parquet" / "options"
    manifest = tmp_path / "manifest.csv"
    archive_root.mkdir()
    archive = archive_root / "2026_q1_option_chain_test.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "NVDA_2026_q1_option_chain.txt",
            "2026-01-02,100,2026-01-16,c,1.2,1.1,1.3,0.2,0.3,10,5,0.5,0.1,0.2,-0.01,0.03\n",
        )
    (raw_root / "NVDA").mkdir(parents=True)

    first = import_option_archives(archive_root, raw_root, parquet_root, manifest, symbols=["NVDA"])
    second = import_option_archives(archive_root, raw_root, parquet_root, manifest, symbols=["NVDA"])

    assert first.files_written == 1
    assert first.parquet_partitions_synced == 1
    assert second.files_written == 0
    assert second.files_skipped == 1
    raw = pd.read_csv(raw_root / "NVDA" / "NVDA_2026_q1_option_chain.csv")
    assert list(raw.columns)[0] == "Trade Date"
    assert raw.loc[0, "Strike"] == 100
    parquet = pd.read_parquet(parquet_root / "symbol=NVDA" / "year=2026" / "quarter=1" / "NVDA_2026_q1.parquet")
    assert parquet.loc[0, "symbol"] == "NVDA"
