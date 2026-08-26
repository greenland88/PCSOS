"""Controlled, isolated repair of confirmed AMZN Batch-1 quote gaps.

This script never writes routed production partitions. It writes only a new
PCSDataAccess-managed repair dataset and records Batch-2 provenance per row.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.onboarding import HistoricalTxtZipAdapter
from pcs.data.storage_schema import OPTION_FIELDS

SOURCE = Path(r"K:\BaiduNetdiskDownload\USDailyOptions")
INPUT = Path("research_outputs/safe_strike_stage4a/candidate_inputs/AMZN.parquet")
ROOT = Path("data/repairs/amzn_batch2_confirmed_gaps")
MANIFEST = ROOT / "manifest.csv"
PROVENANCE = ROOT / "provenance.csv"
DATASET = "options_v2_amzn_batch2_repair"


def cid(row: pd.Series) -> str:
    raw = "|".join(("AMZN", pd.Timestamp(row.date).date().isoformat(),
        pd.Timestamp(row.expiration).date().isoformat(), format(float(row.short_strike), ".15g"),
        format(float(row.long_strike), ".15g")))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def main() -> None:
    candidates = pd.read_parquet(INPUT).copy()
    candidates["date"] = pd.to_datetime(candidates["date"]).dt.date
    candidates["expiration"] = pd.to_datetime(candidates["expiration"]).dt.date
    missing = candidates[candidates[["option_volume", "open_interest", "bid_ask_pct"]].isna().any(axis=1)].copy()
    adapter = HistoricalTxtZipAdapter(SOURCE)
    cache: dict[tuple[int, int], tuple[pd.DataFrame, dict]] = {}
    rows: list[dict] = []
    audit: list[dict] = []
    repaired_at = datetime.now(timezone.utc).isoformat()
    for _, candidate in missing.iterrows():
        period = (candidate.date.year, (candidate.date.month - 1) // 3 + 1)
        if period not in cache:
            cache[period] = adapter.read_period("AMZN", *period)
        vendor, meta = cache[period]
        exact = vendor[
            (vendor.trade_date == candidate.date)
            & (vendor.expiration_date == candidate.expiration)
            & (vendor.strike == float(candidate.short_strike))
            & (vendor.call_put == "p")
        ]
        if exact.empty:
            raise RuntimeError(f"confirmed Batch-2 key not found: {candidate.date} {candidate.expiration} {candidate.short_strike}")
        distinct = exact.drop_duplicates(subset=OPTION_FIELDS)
        if len(distinct) > 1:
            raise RuntimeError("unexpected Batch-2 conflict; frozen policy requires first raw row")
        row = exact.iloc[0]
        valid = all(pd.notna(row[c]) for c in ("bid", "ask", "volume", "open_interest")) and float(row.ask) >= float(row.bid)
        if not valid:
            raise RuntimeError(f"invalid confirmed Batch-2 quote: {candidate.date} {candidate.expiration} {candidate.short_strike}")
        repaired = {c: row[c] for c in OPTION_FIELDS}
        repaired.update({
            "candidate_id": candidate.get("candidate_id") or cid(candidate),
            "primary_authority": "PURCHASED_VENDOR_BATCH_1",
            "repair_source": "PURCHASED_VENDOR_BATCH_2",
            "repair_reason": "BATCH1_GAP_CONFIRMED_BY_BATCH2",
            "batch2_source_archive": meta["source_path"],
            "batch2_source_member": meta["source_member"],
            "batch2_archive_sha256": meta["archive_sha256"],
            "batch2_member_sha256": meta["source_sha256"],
            "batch2_extracted_file_sha256": meta.get("extracted_file_sha256"),
            "batch2_extraction_method": meta.get("extraction_method"),
            "batch2_raw_row_order": int(exact.index[0]),
            "ingestion_timestamp": repaired_at,
            "conflict_policy": "VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW",
        })
        rows.append(repaired)
        audit.append({"candidate_id": repaired["candidate_id"], "trade_date": str(row.trade_date), "expiration_date": str(row.expiration_date), "strike": float(row.strike), "call_put": "p", "status": "BATCH1_GAP_CONFIRMED_BY_BATCH2"})
    repair = pd.DataFrame(rows)
    key = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
    if repair.duplicated(key).any():
        raise RuntimeError("duplicate active repair keys")
    ROOT.mkdir(parents=True, exist_ok=True)
    access = PCSDataAccess(manifest_path=MANIFEST, parquet_root=ROOT)
    partitions = []
    for (year, quarter), part in repair.groupby([pd.to_datetime(repair.trade_date).dt.year, pd.to_datetime(repair.trade_date).dt.quarter]):
        path = access.write_partition(part, DATASET, "AMZN", f"year={year}/quarter={quarter}", source_version="PURCHASED_VENDOR_BATCH_2_REPAIR_OF_BATCH_1_GAPS", filename=f"AMZN_{year}_q{quarter}_repair.parquet")
        partitions.append(str(path))
    pd.DataFrame(audit).to_csv(ROOT / "repair_audit.csv", index=False)
    pd.DataFrame([{"dataset": DATASET, "symbol": "AMZN", "primary_authority": "PURCHASED_VENDOR_BATCH_1", "repair_source": "PURCHASED_VENDOR_BATCH_2", "repair_reason": "BATCH1_GAP_CONFIRMED_BY_BATCH2", "rows": len(repair), "partitions": json.dumps(partitions), "rollback": "remove isolated repair dataset and manifest; production routes unchanged", "timestamp": repaired_at}]).to_csv(PROVENANCE, index=False)
    print(json.dumps({"confirmed_gaps": len(missing), "rows_repaired": len(repair), "partitions": partitions, "duplicate_keys": int(repair.duplicated(key).sum()), "conflicts": 0, "rollback": "available: isolated repair dataset only"}, indent=2))


if __name__ == "__main__":
    from pcs.data.import_boundary import reject_legacy_import_entrypoint
    reject_legacy_import_entrypoint()
