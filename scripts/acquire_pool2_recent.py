"""Acquire one recent option-chain sample per frozen Pool 1 symbol.

This is an ingestion adapter only; evaluation remains behind PCSDataAccess.
"""
from __future__ import annotations
import json, os, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import pandas as pd
from pcs.data.parquet_store import read_option_source
from pcs.data.storage_schema import canonicalize_option_frame
from pcs.data.access import PCSDataAccess

ARCHIVE = Path(r"K:\BaiduNetdiskDownload\USDailyOptions\2026_q3_option_chain_cr7mic.zip")
MANIFEST = Path("data/manifests/options_recent_manifest.csv")
DATASET = "options_recent"

def one(symbol: str) -> dict:
    target = Path("data/parquet") / DATASET / f"symbol={symbol}" / "year=2026/quarter=3" / f"{symbol}_2026_q3.parquet"
    if target.exists(): return {"symbol": symbol, "status": "ALREADY_COMPLETE", "path": str(target)}
    member = f"{symbol}_2026_q3_option_chain.txt"
    with zipfile.ZipFile(ARCHIVE) as z:
        if member not in z.namelist(): return {"symbol": symbol, "status": "NO_RECENT_MEMBER", "reason_codes": ["OPTION_DATA_UNAVAILABLE_IN_SOURCE_WINDOW"]}
        raw = Path("data/raw/options_recent") / symbol / member.replace(".txt", ".csv")
        raw.parent.mkdir(parents=True, exist_ok=True)
        if not raw.exists():
            tmp = raw.with_suffix(".tmp")
            tmp.write_bytes(z.read(member)); os.replace(tmp, raw)
    try:
        frame = read_option_source(raw, symbol)
    except KeyError:
        cols = ["trade_date","strike","expiration_date","call_put","last","bid","ask","bid_iv","ask_iv","open_interest","volume","delta","gamma","vega","theta","rho"]
        frame = pd.read_csv(raw, header=None, names=cols)
        frame["symbol"] = symbol
        for c in ("trade_date", "expiration_date"):
            frame[c] = pd.to_datetime(frame[c], errors="coerce").dt.date
        for c in ("strike","last","bid","ask","bid_iv","ask_iv","open_interest","volume","delta","gamma","vega","theta","rho"):
            frame[c] = pd.to_numeric(frame[c], errors="coerce")
        frame = canonicalize_option_frame(frame)
    access = PCSDataAccess(manifest_path=MANIFEST, parquet_root="data/parquet", source_routes={})
    path = access.write_partition(frame, DATASET, symbol, "year=2026/quarter=3", source_version=str(ARCHIVE), filename=f"{symbol}_2026_q3.parquet")
    return {"symbol": symbol, "status": "IMPORTED", "rows": len(frame), "path": str(path)}

def main() -> None:
    pool = pd.read_parquet("research_outputs/global_pcs_base_universe/pool_1_underlying/underlying_pool.parquet").symbol.astype(str).str.upper().tolist()
    results=[]
    # PCSDataAccess serializes manifest transactions; keep writes serialized
    # while still making the acquisition resumable and idempotent.
    with ThreadPoolExecutor(max_workers=1) as ex:
        futures=[ex.submit(one, s) for s in pool]
        for f in as_completed(futures): results.append(f.result())
    out=pd.DataFrame(results).sort_values("symbol"); Path("research_outputs/global_pcs_base_universe/pool_2_options").mkdir(parents=True,exist_ok=True); out.to_parquet("research_outputs/global_pcs_base_universe/pool_2_options/acquisition_status.parquet",index=False); out.to_csv("research_outputs/global_pcs_base_universe/pool_2_options/acquisition_status.csv",index=False); print(json.dumps(out.groupby("status").size().to_dict(), default=str))

if __name__ == "__main__": main()
