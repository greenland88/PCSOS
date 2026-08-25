"""Restartable research/data-operation migration for daily OHLCV base data."""
from __future__ import annotations
import argparse, csv, time
from pathlib import Path
from .parquet_store import write_daily_partition


def migrate(raw_root="data/raw/daily_forward_adjusted", output_root="data/parquet/daily", manifest="data/manifests/daily_universe_migration.csv"):
    raw=Path(raw_root); out=Path(output_root); mp=Path(manifest); mp.parent.mkdir(parents=True,exist_ok=True)
    existing={}
    if mp.exists():
        for r in csv.DictReader(mp.open(encoding="utf-8")):
            existing[r["symbol"]]=r
    rows=[]; started=time.perf_counter()
    for source in sorted(raw.glob("*_daily_qfq.csv")):
        symbol=source.name[:-len("_daily_qfq.csv")].upper()
        if symbol in existing and existing[symbol].get("status")=="SUCCESS" and int(existing[symbol].get("source_size",-1))==source.stat().st_size:
            rows.append(existing[symbol]); continue
        t=time.perf_counter()
        try:
            parts=write_daily_partition(source,symbol,out)
            row={"symbol":symbol,"source":str(source),"source_size":source.stat().st_size,"partitions":len(parts),"rows_written":sum(n for _,n in parts),"status":"SUCCESS","seconds":round(time.perf_counter()-t,3),"error":""}
        except Exception as exc:
            row={"symbol":symbol,"source":str(source),"source_size":source.stat().st_size,"partitions":0,"rows_written":0,"status":"FAILED","seconds":round(time.perf_counter()-t,3),"error":repr(exc)}
        rows.append(row)
        if len(rows)%25==0:
            with mp.open("w",newline="",encoding="utf-8") as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    with mp.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ["symbol"]); w.writeheader(); w.writerows(rows)
    return {"symbols":len(rows),"success":sum(r.get("status")=="SUCCESS" for r in rows),"failed":sum(r.get("status")=="FAILED" for r in rows),"seconds":round(time.perf_counter()-started,2),"manifest":str(mp)}


def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--raw-root",default="data/raw/daily_forward_adjusted"); p.add_argument("--output-root",default="data/parquet/daily"); p.add_argument("--manifest",default="data/manifests/daily_universe_migration.csv"); a=p.parse_args(argv); print(migrate(a.raw_root,a.output_root,a.manifest))


if __name__=="__main__": main()
