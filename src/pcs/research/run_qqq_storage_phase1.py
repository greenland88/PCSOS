"""Controlled QQQ base migration and integrity audit; never runs a backtest."""
from pathlib import Path
import argparse
import re
import csv, hashlib, time
import pandas as pd
OUT=Path("research_outputs"); PARQUET=Path("data/parquet/options"); MANIFEST=Path("data/manifests/storage_manifest.csv")

def _existing(source, path):
    if not MANIFEST.exists() or not path.exists(): return False
    with MANIFEST.open(encoding="utf-8") as f:
        return any(r.get("source_file")==str(source) and r.get("status")=="SUCCESS" and r.get("schema_version")==str(OPTIONS_SCHEMA_VERSION) for r in csv.DictReader(f))

def main(symbol="QQQ", output_prefix=None):
    raise RuntimeError("LEGACY_STORAGE_READER_DISABLED: use generic canonical onboarding")
    raw_root=Path("data/raw/options")/symbol.upper(); prefix=output_prefix or symbol.lower(); OUT.mkdir(exist_ok=True); started=time.perf_counter(); files=sorted(raw_root.glob(f"{symbol.upper()}_????_q?_option_chain.csv")); progress=[]; before={str(p):(p.stat().st_size,p.stat().st_mtime) for p in files}
    for source in files:
        match=re.search(r"_(\d{4})_q([1-4])_",source.name); year=int(match.group(1)); quarter=int(match.group(2)); target=PARQUET/f"symbol={symbol.upper()}/year={year}/quarter={quarter}/{symbol.upper()}_{year}_q{quarter}.parquet"; t=time.perf_counter()
        if _existing(source,target): progress.append({"symbol":symbol.upper(),"year":year,"quarter":quarter,"source_file":str(source),"rows_read":None,"rows_written":None,"elapsed_seconds":0.0,"status":"SKIP"}); continue
        df=read_option_source(source,symbol); path,rows=write_option_partition(source,symbol,str(PARQUET),year,quarter); raw=source.stat(); append_manifest(str(MANIFEST),{"dataset":"options","symbol":symbol.upper(),"source_file":str(source),"source_size":raw.st_size,"source_modified_time":raw.st_mtime,"row_count":rows,"min_date":df.trade_date.min(),"max_date":df.trade_date.max(),"year":year,"quarter":quarter,"parquet_path":str(path),"schema_version":OPTIONS_SCHEMA_VERSION,"import_timestamp":now_utc(),"status":"SUCCESS"}); progress.append({"symbol":symbol.upper(),"year":year,"quarter":quarter,"source_file":str(source),"rows_read":len(df),"rows_written":rows,"elapsed_seconds":time.perf_counter()-t,"status":"SUCCESS"})
    con=connect(); refresh_views(con); totals=con.execute("select count(*) as n,min(trade_date) as min_date,max(trade_date) as max_date from options where symbol=?",[symbol.upper()]).fetchdf().iloc[0].to_dict(); byq=con.execute("select year,quarter,count(*) as rows from options where symbol=? group by year,quarter order by year,quarter",[symbol.upper()]).fetchdf(); con.close()
    audit=[]
    for item in progress:
        source=Path(item["source_file"]); year=item["year"]; quarter=item["quarter"]; p=PARQUET/f"symbol={symbol.upper()}/year={year}/quarter={quarter}/{symbol.upper()}_{year}_q{quarter}.parquet"; src=read_option_source(source,symbol); pq=pd.read_parquet(p); fields=["trade_date","expiration_date","strike","call_put","last","bid","ask","bid_iv","ask_iv","open_interest","volume","delta","gamma","vega","theta","rho"]
        for field in fields:
            mismatch=0
            if len(src)!=len(pq): mismatch=abs(len(src)-len(pq))
            else:
                a=src[field].reset_index(drop=True); b=pq[field].reset_index(drop=True)
                if pd.api.types.is_numeric_dtype(a): mismatch=int((~((a-b).abs()<=1e-12) & ~(a.isna() & b.isna())).sum())
                else: mismatch=int((a.astype(str)!=b.astype(str)).sum())
            audit.append({"year":year,"quarter":quarter,"sample_type":"full_partition","rows_checked":len(src),"field":field,"mismatch_count":mismatch,"status":"PASS" if mismatch==0 else "FAIL"})
    pd.DataFrame(progress).to_csv(OUT/f"{prefix}_migration_progress.csv",index=False); pd.DataFrame(audit).to_csv(OUT/f"{prefix}_full_integrity_audit.csv",index=False)
    unchanged=all((Path(k).stat().st_size,Path(k).stat().st_mtime)==v for k,v in before.items()); failed=sum(x["status"]=="FAIL" for x in audit); total_csv=sum(x["source_file"] and Path(x["source_file"]).stat().st_size for x in progress); total_pq=sum(p.stat().st_size for p in PARQUET.glob(f"symbol={symbol.upper()}/year=*/quarter=*/*.parquet")); summary={"symbol":symbol.upper(),"quarters_total":len(files),"quarters_success":sum(x["status"]=="SUCCESS" for x in progress),"quarters_skipped":sum(x["status"]=="SKIP" for x in progress),"quarters_failed":0,"total_rows":int(totals["n"]),"min_date":totals["min_date"],"max_date":totals["max_date"],"csv_bytes":total_csv,"parquet_bytes":total_pq,"compression_ratio":total_csv/total_pq if total_pq else None,"runtime_seconds":time.perf_counter()-started,"status":"PASS" if failed==0 and unchanged else "DATA_MISMATCH_STOP"}
    pd.DataFrame([summary]).to_csv(OUT/f"{prefix}_full_migration_summary.csv",index=False); return summary

if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--symbol",default="QQQ"); p.add_argument("--output-prefix"); a=p.parse_args(); print(main(a.symbol,a.output_prefix))
