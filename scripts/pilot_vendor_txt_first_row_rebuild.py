"""Isolated pilot rebuild from purchased vendor TXT files.

This script deliberately writes only to the pilot namespace.  It does not read
ClickHouse and does not modify old canonical storage or production options_v2.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.storage_schema import OPTION_FIELDS

ROOT = Path(r"K:\BaiduNetdiskDownload\USDailyOptions\unzipped")
TICKERS = ("AMD", "HOOD", "META")
OUT_ROOT = Path("data/parquet/options_v2_pilot_vendor_txt_20260820_run2")
MANIFEST = Path("data/manifests/options_v2_pilot_vendor_txt_20260820_run2.csv")
PROVENANCE = Path("data/manifests/options_v2_pilot_vendor_txt_20260820_run2_provenance.csv")
SUMMARY = Path("data/manifests/options_v2_pilot_vendor_txt_20260820_run2.json")
KEY = ["symbol", "trade_date", "expiration_date", "strike", "call_put"]
QUOTES = [c for c in OPTION_FIELDS if c not in KEY]
TXT_COLUMNS = [
    "trade_date", "strike", "expiration_date", "call_put", "last", "bid", "ask",
    "bid_iv", "ask_iv", "open_interest", "volume", "delta", "gamma", "vega",
    "theta", "rho",
]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_txt(symbol: str) -> tuple[pd.DataFrame, list[Path]]:
    files = sorted(ROOT.glob(f"{symbol}_*_option_chain.txt"))
    if not files:
        raise FileNotFoundError(symbol)
    frames = []
    for path in files:
        frame = pd.read_csv(path, header=None, names=TXT_COLUMNS, dtype=str, keep_default_na=True)
        frame["symbol"] = symbol
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.date
        frame["expiration_date"] = pd.to_datetime(frame["expiration_date"], errors="raise").dt.date
        frame["strike"] = pd.to_numeric(frame["strike"], errors="raise")
        frame["call_put"] = frame["call_put"].astype(str).str.lower()
        for col in [c for c in QUOTES if c not in ("call_put", "trade_date", "expiration_date", "strike")]:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frames.append(frame[OPTION_FIELDS])
    return pd.concat(frames, ignore_index=True), files


def conflict_count(frame: pd.DataFrame) -> int:
    # Count quote variants after removing exact full-row duplicates.  This
    # avoids pandas nunique/NaN behavior masking a real quote conflict.
    variants = frame.drop_duplicates(OPTION_FIELDS, keep="first").groupby(KEY, dropna=False, sort=False).size()
    return int(variants.gt(1).sum())


def compare(old: pd.DataFrame, rebuilt: pd.DataFrame) -> dict:
    old = old[OPTION_FIELDS].copy(); rebuilt = rebuilt[OPTION_FIELDS].copy()
    old["strike"] = old["strike"].astype(float); rebuilt["strike"] = rebuilt["strike"].astype(float)
    old = old.drop_duplicates(KEY, keep="first"); rebuilt = rebuilt.drop_duplicates(KEY, keep="first")
    joined = old.merge(rebuilt, on=KEY, how="outer", suffixes=("_old", "_txt"), indicator=True)
    both = joined[joined["_merge"] == "both"]
    field_diffs = {}
    for col in QUOTES:
        field_diffs[col] = int((both[f"{col}_old"].fillna(float("nan")) != both[f"{col}_txt"].fillna(float("nan"))).sum())
    return {"old_unique_keys": int(len(old)), "txt_unique_keys": int(len(rebuilt)),
            "identity_only_old": int((joined["_merge"] == "left_only").sum()),
            "identity_only_txt": int((joined["_merge"] == "right_only").sum()),
            "common_identity_keys": int(len(both)), "field_difference_rows": field_diffs}


def run(symbol: str) -> dict:
    raw, files = load_txt(symbol)
    raw_rows = len(raw)
    exact = int(raw.duplicated(OPTION_FIELDS, keep="first").sum())
    # Stable first raw occurrence: no grouping sort and no quote selection.
    rebuilt = raw.drop_duplicates(KEY, keep="first").reset_index(drop=True)
    remaining_dupes = int(rebuilt.duplicated(KEY).sum())
    remaining_conflicts = conflict_count(rebuilt)
    year_quarter = pd.to_datetime(rebuilt["trade_date"]).map(lambda x: (x.year, (x.month - 1) // 3 + 1)).unique()
    partition_paths = []
    access = PCSDataAccess(manifest_path=MANIFEST, parquet_root=OUT_ROOT.parent)
    # Each source quarter is written independently, preserving source order within quarter.
    for year, quarter in sorted(year_quarter):
        part = rebuilt[(pd.to_datetime(rebuilt.trade_date).dt.year == year) & (((pd.to_datetime(rebuilt.trade_date).dt.month - 1) // 3 + 1) == quarter)].copy()
        rel = f"year={year}/quarter={quarter}"
        target = OUT_ROOT / f"symbol={symbol}" / rel / f"{symbol}_{year}_q{quarter}.parquet"
        source_version = f"historical-vendor-txt:{symbol}:{year}-Q{quarter}:VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW"
        access.write_partition(part, "options_v2_pilot_vendor_txt_20260820_run2", symbol, rel, source_version=source_version, filename=target.name, update_manifest=False, allow_overwrite=True)
        access.update_manifest("options_v2_pilot_vendor_txt_20260820_run2", symbol, part, target, source_version, rel)
        partition_paths.append(str(target))
    old_files = sorted((Path("data/parquet/options") / f"symbol={symbol}").rglob("*.parquet"))
    old = pd.concat([pd.read_parquet(path, columns=OPTION_FIELDS) for path in old_files], ignore_index=True)
    result = {"symbol": symbol, "raw_files": len(files), "raw_paths": [str(p) for p in files],
              "raw_bytes": sum(p.stat().st_size for p in files), "raw_physical_rows": raw_rows,
              "exact_duplicates_removed": exact, "conflicting_keys_resolved_by_first": conflict_count(raw),
              "final_unique_identity_keys": int(len(rebuilt)), "remaining_duplicate_keys": remaining_dupes,
              "remaining_conflicting_keys": remaining_conflicts, "first_trade_date": str(rebuilt.trade_date.min()),
              "last_trade_date": str(rebuilt.trade_date.max()), "partition_paths": partition_paths,
              "output_sha256": {p: sha(Path(p)) for p in partition_paths}, "policy": "VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW",
              "clickhouse_used": False, "canonical_comparison": compare(old, rebuilt),
              "replay_artifact": str(Path("data/parquet/research/variant_b_replay/AMD_duckdb_sample_2d.parquet")) if symbol == "AMD" else None,
              "verdict": "V2 REBUILD DATA VALIDATED — REPLAY NEEDED"}
    access.record_provenance({"source": "purchased historical vendor TXT", "symbol": symbol,
        "dataset": "options_v2_pilot_vendor_txt_20260820_run2", "raw_paths": "|".join(map(str, files)),
        "raw_physical_rows": raw_rows, "exact_duplicates_removed": exact,
        "conflicting_keys_resolved_by_first": conflict_count(raw), "final_unique_identity_keys": len(rebuilt),
        "remaining_duplicate_keys": remaining_dupes, "remaining_conflicting_keys": remaining_conflicts,
        "resolution_policy": "VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW", "clickhouse_used": False,
        "output_sha256": json.dumps(result["output_sha256"], sort_keys=True), "status": "VALIDATED"}, PROVENANCE)
    return result


if __name__ == "__main__":
    from pcs.data.import_boundary import reject_legacy_import_entrypoint
    reject_legacy_import_entrypoint()
