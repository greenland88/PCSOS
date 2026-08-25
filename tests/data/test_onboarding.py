import zipfile

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.import_option_archives import OPTION_COLUMNS
from pcs.data.onboarding import HistoricalTxtZipAdapter, apply_conflict_policy, onboard_ticker, replay_onboarded_partition, validate_txt_clickhouse_overlap


def _row(symbol="ZZZ"):
    return ["2026-08-02", 100.25, "2026-09-18", "p", 1.0, .9, 1.1, .3, .31, 1000, 200, -.25, .01, .02, -.03, -.01]


def _frame(symbol="ZZZ"):
    keys = ["symbol", "trade_date", "expiration_date", "strike", "call_put", "last", "bid", "ask", "bid_iv", "ask_iv", "open_interest", "volume", "delta", "gamma", "vega", "theta", "rho"]
    row = _row()
    values = [symbol, row[0], row[2], row[1], *row[3:]]
    return pd.DataFrame([dict(zip(keys, values))])


def test_txt_zip_adapter_reads_ticker_member_and_preserves_exact_strike(tmp_path):
    archive = tmp_path / "2026_q3_option_chain_test.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        body = ",".join(map(str, _row())) + "\n"
        zf.writestr("ZZZ_2026_q3_option_chain.txt", body)
    frame, meta = HistoricalTxtZipAdapter(tmp_path).read_period("ZZZ", 2026, 3)
    assert frame.iloc[0].strike == 100.25
    assert meta["source_member"].startswith("ZZZ_")
    assert len(meta["source_sha256"]) == 64


def test_overlap_is_mandatory_and_exact_key_sensitive():
    txt = _frame()
    assert validate_txt_clickhouse_overlap(txt, pd.DataFrame()).status == "BLOCKED"
    assert validate_txt_clickhouse_overlap(txt, txt.copy()).status == "READY"
    changed = txt.copy().assign(bid=[.8])
    result = validate_txt_clickhouse_overlap(txt, changed)
    assert result.status == "READY" and result.mismatched_rows == 1


def test_conflict_policy_deduplicates_exact_rows_and_blocks_duplicate_identity():
    base = _frame()
    exact = pd.concat([base, base], ignore_index=True)
    result = apply_conflict_policy(exact, base)
    assert len(result.frame) == 0
    assert result.exact_duplicates_removed == 2
    assert result.conflicts_blocked == 2

    conflicting = base.copy().assign(bid=[.8])
    vendor = pd.concat([base, conflicting], ignore_index=True)
    result = apply_conflict_policy(vendor, pd.DataFrame())
    assert result.conflicts_resolved == 0
    assert result.conflicts_blocked == 2
    assert result.frame.empty


def test_onboarding_writes_only_through_access_and_records_provenance(tmp_path):
    archive = tmp_path / "2026_q3_option_chain_test.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ZZZ_2026_q3_option_chain.txt", ",".join(map(str, _row())) + "\n")
    access = PCSDataAccess(tmp_path / "manifest.csv", tmp_path / "parquet")
    result = onboard_ticker("ZZZ", [(2026, 3)], lambda *_: _frame(), adapter=HistoricalTxtZipAdapter(tmp_path), access=access, dataset="options")
    assert result.status == "READY"
    assert "replay verified" in result.explanation
    assert result.provenance_records == 1
    assert access.read_partition("options", "ZZZ", "year=2026/quarter=3", "ZZZ_2026_q3.parquet").iloc[0].strike == 100.25
    provenance = pd.read_csv(tmp_path / "data_provenance_manifest.csv")
    assert provenance.iloc[0]["source_table"] == "historical_txt"
    assert provenance.iloc[0]["status"] == "READY"


def test_onboarding_blocks_before_any_write_when_overlap_fails(tmp_path):
    archive = tmp_path / "2026_q3_option_chain_test.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ZZZ_2026_q3_option_chain.txt", ",".join(map(str, _row())) + "\n")
    access = PCSDataAccess(tmp_path / "manifest.csv", tmp_path / "parquet")
    result = onboard_ticker("ZZZ", [(2026, 3)], lambda *_: pd.DataFrame(), adapter=HistoricalTxtZipAdapter(tmp_path), access=access, dataset="options")
    assert result.status == "BLOCKED"
    assert not (tmp_path / "manifest.csv").exists()
