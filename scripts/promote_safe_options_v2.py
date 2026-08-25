"""Promote the validated exact-duplicate-only artifacts into isolated options_v2."""
from __future__ import annotations
import hashlib, json, os
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.storage_schema import OPTION_FIELDS
from scripts.safe_rebuild_exact_duplicates import KEY, TARGETS

SAFE = Path("data/manifests/options_v2_safe_rebuild_20260820_provenance.csv")
MAIN_MANIFEST = Path("data/manifests/storage_manifest_options_v2.csv")
RAW_MANIFEST = Path("data/manifests/storage_manifest.csv")
OUT = Path("data/manifests/options_v2_promotion_20260820.json")

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024), b""): h.update(b)
    return h.hexdigest()

def main():
    safe = pd.read_csv(SAFE); access=PCSDataAccess(manifest_path=MAIN_MANIFEST, parquet_root="data/parquet")
    results=[]
    for _, row in safe.iterrows():
        symbol=row.ticker; year, quarter=map(int, row.quarter.replace(" Q", "-").split("-"))
        artifact=Path(row.v2_rebuilt); raw=Path("data/raw/options")/symbol/f"{symbol}_{year}_q{quarter}_option_chain.csv"
        if sha(raw)!=row.source_sha256 or sha(artifact)!=row.output_sha256: raise ValueError(f"checksum mismatch: {symbol} {year} Q{quarter}")
        frame=pd.read_parquet(artifact)[OPTION_FIELDS]
        if len(frame)!=int(row.unique_keys_preserved) or frame[KEY].duplicated().any(): raise ValueError(f"population/key mismatch: {symbol} {year} Q{quarter}")
        conflicts=frame.groupby(KEY,dropna=False)[[c for c in OPTION_FIELDS if c not in KEY]].nunique(dropna=False).gt(1).any(axis=1).sum()
        if conflicts: raise ValueError(f"conflicts: {symbol} {year} Q{quarter}")
        part=f"year={year}/quarter={quarter}"; filename=f"{symbol}_{year}_q{quarter}.parquet"
        target=Path("data/parquet/options_v2")/f"symbol={symbol}"/part/filename
        source_version=f"safe-rebuild:20260820:{row.output_sha256}"
        access.write_partition(frame,"options_v2",symbol,part,source_version=source_version,update_manifest=False,allow_overwrite=True,filename=filename)
        access.update_manifest("options_v2",symbol,frame,target,source_version,part,replace_existing=True)
        access.record_provenance({"source":"safe_rebuild_artifact","symbol":symbol,"quarter":f"{year} Q{quarter}","dataset":"options_v2","source_sha256":row.source_sha256,"artifact_sha256":row.output_sha256,"raw_rows":int(row.raw_rows),"exact_duplicates_removed":int(row.exact_duplicates_removed),"conflicting_keys":0,"unique_keys_preserved":int(row.unique_keys_preserved),"promotion":"PCSDataAccess","status":"PROMOTED"})
        results.append({"ticker":symbol,"quarter":f"{year} Q{quarter}","status":"PROMOTED"})
    temp = OUT.with_name(f".{OUT.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(results,indent=2),encoding="utf-8")
        os.replace(temp, OUT)
    finally:
        temp.unlink(missing_ok=True)
    print("promoted",len(results))

if __name__=="__main__": main()
