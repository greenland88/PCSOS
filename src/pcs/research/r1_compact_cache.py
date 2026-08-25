"""Build and audit the immutable compact OHLCV cache for R1 validation."""
from pathlib import Path
from datetime import datetime, timezone
import json, hashlib, duckdb, pandas as pd

ROOT=Path(__file__).resolve().parents[3]; OUT=ROOT/"research_outputs"; CACHE=ROOT/"research_cache"/"r1_external_v1"/"ohlcv_compact"; UNIVERSE=OUT/"r1_external_validation_universe_v1.csv"; HASH=OUT/"r1_external_validation_universe_v1.sha256"

def build():
    u=pd.read_csv(UNIVERSE); expected=HASH.read_text().split()[0]; actual=hashlib.sha256("\n".join(u.ticker).encode()).hexdigest(); assert expected==actual and len(u)==8041
    CACHE.mkdir(parents=True,exist_ok=True); con=duckdb.connect(); con.execute("set enable_progress_bar=false")
    con.register("universe",u[["ticker"]]); target=(CACHE/"ohlcv_compact.parquet").as_posix()
    con.execute(f"COPY (SELECT p.symbol AS ticker, p.date, p.open, p.high, p.low, p.close, p.volume FROM read_parquet('data/parquet/daily/**/*.parquet', union_by_name=true, hive_partitioning=true) p JOIN universe u ON p.symbol=u.ticker ORDER BY p.symbol,p.date) TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 1000000)")
    con.close(); return audit()

def audit():
    con=duckdb.connect(); con.execute("set enable_progress_bar=false"); f=(CACHE/"ohlcv_compact.parquet").as_posix(); u=pd.read_csv(UNIVERSE); q=f"select count(*) row_count,count(distinct ticker) symbol_count,min(date) min_date,max(date) max_date,sum(case when ticker is null or date is null or open is null or high is null or low is null or close is null or volume is null then 1 else 0 end) missing_rows from read_parquet('{f}')"; r=con.execute(q).fetchdf().iloc[0].to_dict(); dup=con.execute(f"select count(*) n from (select ticker,date,count(*) c from read_parquet('{f}') group by ticker,date having c>1)").fetchone()[0]; present=set(con.execute(f"select distinct ticker from read_parquet('{f}')").fetchdf().ticker); missing=sorted(set(u.ticker)-present); meta={"source_data_version":"daily_parquet_v1","universe_version":"R1_EXTERNAL_UNIVERSE_V1","r1_version":"R1_FROZEN_V1","cache_created_at":datetime.now(timezone.utc).isoformat(),"row_count":int(r["row_count"]),"symbol_count":int(r["symbol_count"]),"min_date":str(r["min_date"]),"max_date":str(r["max_date"]),"duplicate_ticker_date_rows":int(dup),"missing_required_rows":int(r["missing_rows"]),"missing_symbols":missing,"universe_checksum":HASH.read_text().split()[0]}; (CACHE/"cache_metadata.json").write_text(json.dumps(meta,indent=2),encoding="utf-8"); con.close(); return meta

if __name__=="__main__": print(build())
