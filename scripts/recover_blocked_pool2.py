from __future__ import annotations
import os, zipfile
from pathlib import Path
import pandas as pd
from pcs.data.parquet_store import read_option_source
from pcs.data.storage_schema import canonicalize_option_frame
from pcs.data.access import PCSDataAccess

ROOT=Path("research_outputs/global_pcs_base_universe"); DATASET="options_recent"; MANIFEST=Path("data/manifests/options_recent_manifest.csv")
SOURCES=[(2,Path(r"K:\BaiduNetdiskDownload\USDailyOptions\2026_q2_option_chain_2ihcl8.zip")),(1,Path(r"K:\BaiduNetdiskDownload\USDailyOptions\2026_q1_option_chain_jrk4dv.zip"))]
def read_raw(raw:Path,symbol:str):
    try: return read_option_source(raw,symbol)
    except KeyError:
        cols=["trade_date","strike","expiration_date","call_put","last","bid","ask","bid_iv","ask_iv","open_interest","volume","delta","gamma","vega","theta","rho"]
        f=pd.read_csv(raw,header=None,names=cols); f["symbol"]=symbol
        for c in ("trade_date","expiration_date"): f[c]=pd.to_datetime(f[c],errors="coerce").dt.date
        for c in ("strike","last","bid","ask","bid_iv","ask_iv","open_interest","volume","delta","gamma","vega","theta","rho"): f[c]=pd.to_numeric(f[c],errors="coerce")
        return canonicalize_option_frame(f)
def main():
    pool=set(pd.read_parquet(ROOT/"pool_1_underlying"/"underlying_pool.parquet").symbol.astype(str).str.upper()); q3={x.split('_2026_q3_',1)[0] for x in zipfile.ZipFile(r"K:\BaiduNetdiskDownload\USDailyOptions\2026_q3_option_chain_cr7mic.zip").namelist()}; blocked=pool-q3; results=[]
    for q,zpath in SOURCES:
        with zipfile.ZipFile(zpath) as z:
            names=set(z.namelist())
            for s in sorted(blocked):
                if any(r.get("symbol")==s and r.get("status")=="IMPORTED" for r in results): continue
                member=f"{s}_2026_q{q}_option_chain.txt"
                if member not in names: continue
                raw=Path("data/raw/options_recent")/s/member.replace('.txt','.csv'); raw.parent.mkdir(parents=True,exist_ok=True)
                if not raw.exists():
                    tmp=raw.with_suffix('.tmp'); tmp.write_bytes(z.read(member)); os.replace(tmp,raw)
                f=read_raw(raw,s); a=PCSDataAccess(manifest_path=MANIFEST,parquet_root="data/parquet",source_routes={}); path=a.write_partition(f,DATASET,s,f"year=2026/quarter={q}",source_version=str(zpath),filename=f"{s}_2026_q{q}.parquet"); results.append({"symbol":s,"quarter":q,"status":"IMPORTED","rows":len(f),"path":str(path)})
    out=pd.DataFrame(results); out.to_csv(ROOT/"pool_2_options"/"blocked_recovery_imports.csv",index=False); print(out.to_string(index=False)); print('RECOVERED',len(out))
if __name__=='__main__':
    from pcs.data.import_boundary import reject_legacy_import_entrypoint
    reject_legacy_import_entrypoint()
