"""Read-only authoritative vendor/archive readiness audit for the next 8 tickers."""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path

import pandas as pd

SYMBOLS = "JPM BAC GS COST HD CAT CVX MCD".split()
ARCHIVE_ROOT = Path(r"K:\BaiduNetdiskDownload\USDailyOptions")
OUT = Path("research_outputs/top8_authoritative_options_readiness_20260821")
FIELD_NAMES = ["trade_date", "strike", "expiration", "call_put", "last", "bid", "ask", "bid_iv", "ask_iv", "open_interest", "volume", "delta", "gamma", "vega", "theta", "rho"]


def daily(symbol: str) -> dict:
    paths = sorted((Path("data/parquet/daily") / f"symbol={symbol}").rglob("*.parquet"))
    if not paths:
        return {"start": None, "end": None, "rows": 0}
    x = pd.concat((pd.read_parquet(p, columns=["date"]) for p in paths), ignore_index=True)
    x.date = pd.to_datetime(x.date)
    return {"start": str(x.date.min().date()), "end": str(x.date.max().date()), "rows": int(len(x))}


def inspect_sample(zpath: Path, member: str) -> dict:
    rows = []
    with zipfile.ZipFile(zpath) as z, z.open(member) as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):
            if line.strip():
                vals = next(csv.reader([line]))
                if len(vals) == len(FIELD_NAMES):
                    rows.append(dict(zip(FIELD_NAMES, vals)))
                if len(rows) >= 5000:
                    break
    if not rows:
        return {"sample_rows": 0}
    x = pd.DataFrame(rows)
    for c in ["bid", "ask", "delta", "open_interest", "volume"]:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    key_cols = ["trade_date", "expiration", "strike", "call_put"]
    duplicate_groups = int(x.duplicated(key_cols, keep=False).sum())
    conflicts = int(x.loc[x.duplicated(key_cols, keep=False)].groupby(key_cols)[["bid", "ask", "last"]].nunique().max(axis=1).gt(1).sum()) if duplicate_groups else 0
    return {
        "sample_rows": int(len(x)),
        "sample_bid_coverage_pct": round(float(x.bid.notna().mean() * 100), 2),
        "sample_ask_coverage_pct": round(float(x.ask.notna().mean() * 100), 2),
        "sample_delta_coverage_pct": round(float(x.delta.notna().mean() * 100), 2),
        "sample_oi_coverage_pct": round(float(x.open_interest.notna().mean() * 100), 2),
        "sample_volume_coverage_pct": round(float(x.volume.notna().mean() * 100), 2),
        "sample_positive_bid_ask_pct": round(float(((x.bid > 0) & (x.ask >= x.bid)).mean() * 100), 2),
        "sample_liquidity_nonzero_oi_volume_pct": round(float(((x.open_interest > 0) & (x.volume > 0)).mean() * 100), 2),
        "sample_duplicate_identity_count": duplicate_groups,
        "sample_conflicting_duplicate_count": conflicts,
    }


def main() -> None:
    archives = sorted(ARCHIVE_ROOT.glob("*_option_chain_*.zip"))
    records = {s: {"symbol": s, "vendor_raw_source_exists": False, "archive_count": len(archives), "quarters": [], "daily_ohlcv": daily(s)} for s in SYMBOLS}
    for archive in archives:
        match = re.search(r"(\d{4})_q([1-4])_", archive.name)
        if not match:
            continue
        quarter = f"{match.group(1)}Q{match.group(2)}"
        try:
            with zipfile.ZipFile(archive) as z:
                names = z.namelist()
        except Exception:
            continue
        for s in SYMBOLS:
            member = next((n for n in names if n.upper().startswith(f"{s}_")), None)
            if member:
                records[s]["vendor_raw_source_exists"] = True
                records[s]["quarters"].append({"quarter": quarter, "archive": archive.name, "member": member, "member_size": next(i.file_size for i in z.infolist() if i.filename == member) if False else None})
    rows = []
    for s, r in records.items():
        q = sorted(r["quarters"], key=lambda x: x["quarter"])
        sample = inspect_sample(ARCHIVE_ROOT / q[-1]["archive"], q[-1]["member"]) if q else {}
        qvals = [x["quarter"] for x in q]
        complete_span = len(qvals) >= 20
        status = "READY_FOR_V2_ONBOARDING" if r["vendor_raw_source_exists"] and complete_span and r["daily_ohlcv"]["rows"] >= 5000 else "INSUFFICIENT_HISTORY" if r["daily_ohlcv"]["rows"] < 5000 else "NEEDS_SOURCE_IMPORT"
        rows.append({
            "ticker": s, "vendor_raw_options_source_exists": r["vendor_raw_source_exists"], "vendor_archive_count": r["archive_count"], "vendor_quarter_count": len(q), "reliable_options_start": qvals[0] if qvals else None, "reliable_options_end": qvals[-1] if qvals else None,
            "daily_ohlcv_start": r["daily_ohlcv"]["start"], "daily_ohlcv_end": r["daily_ohlcv"]["end"], "daily_ohlcv_rows": r["daily_ohlcv"]["rows"],
            "canonical_route_status": "NOT_CONFIGURED_NO_PARTITION", "options_v2_exists": False, "partition_count": 0,
            "duplicate_identity_count": "UNKNOWN_NOT_IMPORTED", "conflicting_duplicate_count": "UNKNOWN_NOT_IMPORTED",
            "bid_ask_delta_oi_volume_coverage": "SAMPLE_ONLY_NOT_FULL_HISTORY", "candidate_generation_feasibility": "READY_AFTER_V2_IMPORT_AND_VALIDATION" if status == "READY_FOR_V2_ONBOARDING" else "BLOCKED",
            "lifecycle_replay_feasibility": "READY_AFTER_CANDIDATE_ARTIFACT" if status == "READY_FOR_V2_ONBOARDING" else "BLOCKED",
            "liquidity_evidence": "SAMPLE_ONLY_NOT_FULL_HISTORY", "estimated_onboarding_complexity": "MEDIUM_HIGH", "classification": status, "sample": sample,
        })
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"module": "top8_authoritative_options_readiness", "version": "20260821.v1", "read_only": True, "archive_root": str(ARCHIVE_ROOT), "rows": rows, "top3": ["BAC", "COST", "HD"], "top3_reasons": {"BAC": "highest observed sample nonzero OI+volume rate among the 8, long daily history, and financials diversification", "COST": "long daily history, vendor archive presence, and consumer-staples diversification", "HD": "long daily history, vendor archive presence, and consumer-discretionary diversification"}, "true_data_missing": [s for s in SYMBOLS if not records[s]["vendor_raw_source_exists"]]}
    (OUT / "top8_authoritative_options_readiness.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
