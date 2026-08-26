"""Per-key ClickHouse authoritative repair for the two blocked 2025 Q3 quarters."""
from __future__ import annotations
import hashlib, json, os, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq
from pcs.data.access import PCSDataAccess
from pcs.data.storage_schema import OPTION_FIELDS
from scripts.safe_rebuild_exact_duplicates import _load, KEY

TABLE="firstrate.options_kline_1d"
MAP={"TradeDate":"trade_date","Symbol":"symbol","ExpiryDate":"expiration_date","Strike":"strike","CallPut":"call_put","LastTradePrice":"last","BidPrice":"bid","AskPrice":"ask","BidImpliedVolatilities":"bid_iv","AskImpliedVolatilities":"ask_iv","OpenInterest":"open_interest","Volume":"volume","Delta":"delta","Gamma":"gamma","Vega":"vega","Theta":"theta","Rho":"rho"}
FIELDS=list(MAP.values())

def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()

def fetch(symbol):
 sql="SELECT "+','.join(f'{a} AS {b}' for a,b in MAP.items())+f" FROM {TABLE} WHERE Symbol='{symbol}' AND TradeDate BETWEEN '2025-07-01' AND '2025-09-30' FORMAT Parquet"
 req=urllib.request.Request('http://db.base32.cn:8123/?'+urllib.parse.urlencode({'user':os.getenv('CLICKHOUSE_USER','hisdata230'),'password':os.getenv('CLICKHOUSE_PASSWORD')}),data=sql.encode(),method='POST')
 data=urllib.request.urlopen(req,timeout=300).read(); p=Path('data')/f'.{symbol}_2025_q3_clickhouse.parquet'; p.write_bytes(data)
 out=pq.read_table(p).to_pandas(); p.unlink(missing_ok=True)
 for c in ('trade_date','expiration_date'): out[c]=pd.to_datetime(out[c]).dt.date
 out['strike']=pd.to_numeric(out['strike']); out['call_put']=out['call_put'].astype(str).str.lower(); out['symbol']=symbol
 return out[FIELDS], sql

def equal(a,b):
 for c in FIELDS:
  x,y=a[c],b[c]
  if pd.isna(x) and pd.isna(y): continue
  if x != y: return False
 return True

def main():
 os.environ.setdefault('CLICKHOUSE_PASSWORD','his@9LP1Zx'); access=PCSDataAccess(manifest_path='data/manifests/storage_manifest_options_v2.csv',parquet_root='data/parquet'); results=[]
 for symbol in ('TSLA','AMZN'):
  raw_path=Path('data/raw/options')/symbol/f'{symbol}_2025_q3_option_chain.csv'; raw=_load(raw_path,symbol); ch,query=fetch(symbol); fetch_ts=datetime.now(timezone.utc).isoformat()
  ch_groups=ch.groupby(KEY,dropna=False,sort=False); ch_exact=ch.drop_duplicates(FIELDS,keep=False)
  ch_dup_exact=int(len(ch)-len(ch.drop_duplicates(FIELDS)))
  ch_conf_keys=0
  for _,x in ch_groups:
   if len(x)>1 and x[FIELDS].drop_duplicates().shape[0]>1: ch_conf_keys+=1
  if ch_conf_keys: raise RuntimeError(f'{symbol}: ClickHouse conflicting duplicate keys={ch_conf_keys}')
  ch_one=ch.drop_duplicates(FIELDS,keep='first').set_index(KEY,drop=False)
  out=[]; resolved_a=resolved_b=unresolved=raw_conflicts=0; raw_nonconf=raw.drop_duplicates(FIELDS,keep='first')
  for k,x in raw.groupby(KEY,dropna=False,sort=False):
   variants=x.drop_duplicates(FIELDS,keep='first')
   if len(variants)==1: out.append(variants.iloc[0]); continue
   raw_conflicts+=1
   if k not in ch_one.index: unresolved+=1; continue
   auth=ch_one.loc[k]
   hits=[equal(variants.iloc[i],auth) for i in range(len(variants))]
   if sum(hits)!=1: unresolved+=1; continue
   out.append(variants.iloc[hits.index(True)]); resolved_a+=hits[0]; resolved_b+=hits[1] if len(hits)>1 else 0
  if unresolved or raw_conflicts != (resolved_a+resolved_b): raise RuntimeError(f'{symbol}: unresolved={unresolved}, conflicts={raw_conflicts}, resolved={resolved_a+resolved_b}')
  repaired=pd.DataFrame(out,columns=OPTION_FIELDS).sort_values(KEY,kind='mergesort').reset_index(drop=True)
  if repaired[KEY].duplicated().any(): raise RuntimeError(f'{symbol}: output duplicate keys')
  target=Path('data/parquet/options_v2')/f'symbol={symbol}'/'year=2025'/'quarter=3'/f'{symbol}_2025_q3.parquet'
  if target.exists(): raise FileExistsError(f'write target already exists: {target}')
  source_version=f'authoritative-per-key-source-match:{TABLE}:2025-07-01:2025-09-30'
  access.write_partition(repaired,'options_v2',symbol,'year=2025/quarter=3',source_version=source_version,filename=target.name,update_manifest=False)
  access.update_manifest('options_v2',symbol,repaired,target,source_version,'year=2025/quarter=3',replace_existing=True)
  prov={'source':'raw historical quarter','raw_file_path':str(raw_path),'raw_sha256':sha(raw_path),'conflict_resolution_source':TABLE,'clickhouse_query_range':'2025-07-01..2025-09-30','clickhouse_fetch_timestamp':fetch_ts,'conflicting_keys_examined':raw_conflicts,'keys_resolved_by_raw_a':resolved_a,'keys_resolved_by_raw_b':resolved_b,'unresolved_keys':unresolved,'exact_clickhouse_duplicates_removed':ch_dup_exact,'conflicting_clickhouse_duplicate_keys':ch_conf_keys,'final_quarter_checksum':sha(target),'resolution_rule':'AUTHORITATIVE_PER_KEY_SOURCE_MATCH','dataset':'options_v2','symbol':symbol,'status':'PROMOTED'}
  access.record_provenance(prov); results.append(prov)
 Path('data/manifests/options_v2_authoritative_q3_repair_20260820.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
 for r in results: print(f"{r['symbol']} | {r['conflicting_keys_examined']} | {r['keys_resolved_by_raw_a']+r['keys_resolved_by_raw_b']} | {r['unresolved_keys']} | 0 | 0 | COMPLETE | NOT_STARTED | V2 Q3 AUTHORITATIVE REPAIR VALIDATED")

if __name__=='__main__':
  from pcs.data.import_boundary import reject_legacy_import_entrypoint
  reject_legacy_import_entrypoint()
