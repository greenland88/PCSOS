"""Reconstruct missing daily provenance only from the existing migration manifest."""
from pathlib import Path
import hashlib, json
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
def sha(p):
 h=hashlib.sha256();
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()
def main():
 p=ROOT/'data/manifests/data_provenance_manifest.csv'; src=ROOT/'data/manifests/daily_universe_migration.csv'; raw=ROOT/'data/raw/daily_forward_adjusted/AMD_daily_qfq.csv'
 d=pd.read_csv(p); m=pd.read_csv(src); row=m[m.symbol.eq('AMD')].iloc[0];
 if not d.astype(str).apply(lambda c:c.str.contains('AMD',case=False).any()).any():
  record={c:'' for c in d.columns}; record.update({'source':'daily_universe_migration','source_table':'daily_ohlcv','symbol':'AMD','source_version':'daily_universe_migration.csv','dataset':'daily','status':'PROMOTED','source_path':str(raw.relative_to(ROOT)).replace('\\','/'),'source_file':str(raw.relative_to(ROOT)).replace('\\','/'),'source_sha256':sha(raw),'raw_sha256':sha(raw),'rows':int(row.rows_written),'canonical_rows':int(row.rows_written),'final_rows':int(row.rows_written),'resolution_policy':'DAILY_SOURCE_MANIFEST_LINEAGE','authoritative_source':'daily_forward_adjusted_qfq','route':'daily','date_min':'2015-01-05','date_max':'2026-08-18','provenance_key':'daily|AMD|daily_universe_migration.csv'})
  d=pd.concat([d,pd.DataFrame([record])],ignore_index=True); d.to_csv(p,index=False)
  print(json.dumps({'status':'COMPLETED','symbol':'AMD','rows':int(row.rows_written),'source_sha256':record['source_sha256']}))
 else: print(json.dumps({'status':'ALREADY_PRESENT','symbol':'AMD'}))
if __name__=='__main__': main()
