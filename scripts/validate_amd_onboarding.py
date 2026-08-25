"""Validate AMD onboarding increment, overlap, and final identity integrity."""
from pathlib import Path
import duckdb, pandas as pd
from pcs.data.storage_schema import OPTION_FIELDS

OLD='data/parquet/options/symbol=AMD/**/*.parquet'
NEW='data/parquet/options_v2_onboarding_amd_20260820/symbol=AMD/**/*.parquet'
KEY=['symbol','trade_date','expiration_date','strike','call_put']
QUOTES=[c for c in OPTION_FIELDS if c not in KEY]
c=duckdb.connect()
q=f'''WITH x AS (SELECT * FROM read_parquet('{NEW}')), k AS (SELECT symbol,trade_date,expiration_date,strike,call_put,count(*) n,count(DISTINCT (last,bid,ask,bid_iv,ask_iv,open_interest,volume,delta,gamma,vega,theta,rho)) v FROM x GROUP BY ALL) SELECT count(*) physical,min(trade_date),max(trade_date),count(DISTINCT (symbol,trade_date,expiration_date,strike,call_put)) unique_keys,(SELECT coalesce(sum(n-1) FILTER(WHERE n>1),0) FROM k),(SELECT count(*) FILTER(WHERE v>1) FROM k) conflicts FROM x'''
print('FINAL',c.execute(q).fetchone())
q2=f'''WITH o AS (SELECT * FROM read_parquet('{OLD}') WHERE trade_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-18'), n AS (SELECT * FROM read_parquet('{NEW}') WHERE trade_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-18'), j AS (SELECT o.* EXCLUDE(symbol,trade_date,expiration_date,strike,call_put),n.* EXCLUDE(symbol,trade_date,expiration_date,strike,call_put) FROM o JOIN n USING(symbol,trade_date,expiration_date,strike,call_put)) SELECT (SELECT count(*) FROM o),(SELECT count(*) FROM n),(SELECT count(DISTINCT (symbol,trade_date,expiration_date,strike,call_put)) FROM o),(SELECT count(DISTINCT (symbol,trade_date,expiration_date,strike,call_put)) FROM n),(SELECT count(*) FROM j)'''
print('OVERLAP_COUNTS',c.execute(q2).fetchone())
old=c.execute(f"SELECT * FROM read_parquet('{OLD}') WHERE trade_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-18'").fetchdf(); new=c.execute(f"SELECT * FROM read_parquet('{NEW}') WHERE trade_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-18'").fetchdf()
if not old.empty and not new.empty:
    j=old.merge(new,on=KEY,suffixes=('_old','_new')); diffs={col:int((j[f'{col}_old'].fillna(-999999)!=j[f'{col}_new'].fillna(-999999)).sum()) for col in QUOTES}; print('OVERLAP_FIELD_DIFFERENCES',diffs)
c.close()
