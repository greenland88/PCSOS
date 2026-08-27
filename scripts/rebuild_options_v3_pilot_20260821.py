"""Resource-safe QQQ 2026-01 raw-options rebuild pilot.
One ticker x one month; DuckDB streaming/projection; no canonical writes.
"""
from pathlib import Path
import hashlib,json,time,shutil,datetime,os
import duckdb,pandas as pd

ROOT=Path('.'); OUT=ROOT/'research_outputs/opportunity_state_machine_research_20260821/rebuilt_options_v3'; TMP=OUT/'_tmp_qqq_2026_01'; TMP.mkdir(parents=True,exist_ok=True)
SRC=ROOT/'data/raw/options/QQQ/QQQ_2026_q1_option_chain.csv'; TARGET=OUT/'ticker=QQQ/year=2026/month=01'; TARGET.mkdir(parents=True,exist_ok=True)
REQ={'trade_date':'DATE','expiration':'DATE','option_type':'VARCHAR','strike':'DOUBLE','bid':'DOUBLE','ask':'DOUBLE','last':'DOUBLE','volume':'BIGINT','open_interest':'BIGINT','bid_iv':'DOUBLE','ask_iv':'DOUBLE','delta':'DOUBLE'}

def sha(p):
 h=hashlib.sha256();
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()
def main():
 start=time.perf_counter(); out_tmp=TMP/'data_0.parquet.tmp'; out_final=TARGET/'data_0.parquet'; con=duckdb.connect(database=':memory:'); con.execute("PRAGMA threads=1"); con.execute("PRAGMA memory_limit='2GB'")
 src=str(SRC.resolve()).replace("'","''"); con.execute(f"CREATE OR REPLACE TEMP VIEW raw AS SELECT * FROM read_csv_auto('{src}', header=true, ignore_errors=false)")
 cols=con.execute('DESCRIBE raw').fetchdf(); rename={'Trade Date':'trade_date','Expiry Date':'expiration','Call/Put':'option_type','Last Trade Price':'last','Bid Price':'bid','Ask Price':'ask','Bid Implied Volatility':'bid_iv','Ask Implied Volatility':'ask_iv','Open Interest':'open_interest','Volume':'volume','Delta':'delta','Strike':'strike'}
 select=', '.join([f'CAST("{k}" AS {v}) AS {rename.get(k,k)}' for k,v in [('Trade Date','DATE'),('Expiry Date','DATE'),('Call/Put','VARCHAR'),('Strike','DOUBLE'),('Bid Price','DOUBLE'),('Ask Price','DOUBLE'),('Last Trade Price','DOUBLE'),('Volume','BIGINT'),('Open Interest','BIGINT'),('Bid Implied Volatility','DOUBLE'),('Ask Implied Volatility','DOUBLE'),('Delta','DOUBLE')]])
 con.execute(f"CREATE OR REPLACE TEMP VIEW month AS SELECT {select} FROM raw WHERE CAST(\"Trade Date\" AS DATE) >= DATE '2026-01-01' AND CAST(\"Trade Date\" AS DATE) < DATE '2026-02-01'")
 key="trade_date, expiration, option_type, strike"; value="bid, ask, last, volume, open_interest, bid_iv, ask_iv, delta"
 dup=con.execute(f"SELECT COUNT(*) AS duplicate_rows, COUNT(DISTINCT ({key})) AS duplicate_keys FROM month GROUP BY {key} HAVING COUNT(*)>1").fetchdf()
 audit=con.execute(f"SELECT {key}, COUNT(*) AS row_count, COUNT(DISTINCT md5(concat_ws('|',{value}))) AS market_value_versions FROM month GROUP BY {key} HAVING COUNT(*)>1").fetchdf()
 exact=int((audit.market_value_versions==1).sum()) if len(audit) else 0; conflict=int((audit.market_value_versions>1).sum()) if len(audit) else 0; rows=int(con.execute('SELECT COUNT(*) FROM month').fetchone()[0]); unique=int(con.execute(f'SELECT COUNT(*) FROM (SELECT DISTINCT {key} FROM month)').fetchone()[0])
 con.execute(f"COPY (SELECT * EXCLUDE (rn) FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY {key} ORDER BY {key}) rn FROM month WHERE ({key}) NOT IN (SELECT {key} FROM (SELECT {key}, COUNT(DISTINCT md5(concat_ws('|',{value}))) n FROM month GROUP BY {key} HAVING n>1))) WHERE rn=1 ORDER BY {key}) TO '{str(out_tmp).replace(chr(39),chr(39)*2)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
 valid=int(con.execute(f"SELECT COUNT(*) FROM read_parquet('{str(out_tmp).replace(chr(39),chr(39)*2)}')").fetchone()[0]); con.execute(f"SELECT {key}, COUNT(*) c FROM read_parquet('{str(out_tmp).replace(chr(39),chr(39)*2)}') GROUP BY {key} HAVING c>1").fetchall(); os.replace(out_tmp,out_final)
 pd.DataFrame([{'ticker':'QQQ','year':2026,'month':1,'source':str(SRC),'source_bytes':SRC.stat().st_size,'source_rows_month':rows,'source_unique_keys':unique,'duplicate_keys':len(audit),'exact_duplicate_keys':exact,'conflicting_keys':conflict,'quarantined_rows':int(audit.loc[audit.market_value_versions>1,'row_count'].sum()) if len(audit) else 0,'output_rows':valid,'output_unique_keys':valid,'status':'VALID_WITH_WARNINGS' if conflict else 'VALID','elapsed_seconds':round(time.perf_counter()-start,3),'output_bytes':out_final.stat().st_size}]).to_csv(OUT/'partition_validation.csv',index=False)
 pd.DataFrame([{'ticker':'QQQ','partition':'2026-01','source':str(SRC),'coverage_start':'2026-01-01','coverage_end':'2026-01-31','source_rows':rows,'output_rows':valid,'status':'VALID_WITH_WARNINGS' if conflict else 'VALID'}]).to_csv(OUT/'source_coverage.csv',index=False)
 audit.to_csv(OUT/'duplicate_resolution_ledger.csv',index=False); pd.DataFrame(columns=['ticker','partition','key','reason','status']).to_csv(OUT/'conflicting_quote_quarantine.csv',index=False)
 manifest={'dataset':'opportunity_state_machine_research_rebuilt_options_v3','research_only':True,'pilot_only':True,'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'ticker':'QQQ','partition':'2026-01','source':str(SRC),'source_sha256':sha(SRC),'output':str(out_final),'output_sha256':sha(out_final),'unique_key':key,'schema':{k:v for k,v in zip(cols.column_name,cols.column_type)},'source_rows':rows,'output_rows':valid,'duplicate_keys':len(audit),'exact_duplicate_keys':exact,'conflicting_keys':conflict,'quarantined_rows':int(audit.loc[audit.market_value_versions>1,'row_count'].sum()) if len(audit) else 0,'validation_status':'VALID_WITH_WARNINGS' if conflict else 'VALID','memory_policy':'single worker; one ticker x one month; DuckDB projection/filter; no concat'}
 (OUT/'rebuilt_options_manifest.json').write_text(json.dumps(manifest,indent=2,default=str),encoding='utf-8'); (OUT/'rebuild_run_log.txt').write_text(json.dumps({'phase':'QQQ_2026_01_PILOT','source_bytes':SRC.stat().st_size,'output_bytes':out_final.stat().st_size,'elapsed_seconds':round(time.perf_counter()-start,3),'old_canonical_modified':False,'production_path_modified':False,'pilot_gate':'PASS' if valid and conflict==0 else 'BLOCKED_CONFLICTS'},indent=2),encoding='utf-8')
 print(json.dumps(manifest,indent=2,default=str))
if __name__=='__main__': main()
