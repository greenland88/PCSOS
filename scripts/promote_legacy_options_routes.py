"""Promote existing approved legacy option partitions into options_v2 routes."""
from pathlib import Path
import hashlib, json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.onboarding import activate_authoritative_route, apply_conflict_policy

ROOT=Path(__file__).resolve().parents[1]
def sha(p):
 h=hashlib.sha256();
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()
def main():
 legacy=pd.read_csv(ROOT/'data/manifests/storage_manifest.csv')
 target_manifest=ROOT/'data/manifests/storage_manifest_options_v2.csv'
 access=PCSDataAccess(manifest_path=target_manifest,parquet_root=ROOT/'data/parquet')
 results=[]
 for symbol in ('SPY','MU'):
  rows=legacy[(legacy.dataset=='options') & legacy.symbol.astype(str).str.upper().eq(symbol)].drop_duplicates(['year','quarter','parquet_path'])
  if rows.empty: results.append({'symbol':symbol,'status':'SOURCE_NOT_FOUND'}); continue
  for _,meta in rows.iterrows():
   part=f"year={int(meta.year)}/quarter={int(meta.quarter)}"; source=Path(str(meta.parquet_path));
   if not source.exists(): raise FileNotFoundError(source)
   frame=pd.read_parquet(source); policy=apply_conflict_policy(frame, pd.DataFrame(columns=frame.columns)); frame=policy.frame; frame=access.validate_schema(frame,'options_v2'); target=ROOT/'data/parquet/options_v2'/f'symbol={symbol}'/part/f'{symbol}_{int(meta.year)}_q{int(meta.quarter)}.parquet'
   if target.exists(): status='ALREADY_PRESENT'
   else:
    access.write(frame,'options_v2',symbol,part,source_version=f"legacy-promotion:{meta.source_file}",allow_overwrite=False,update_manifest=True,filename=target.name); status='PROMOTED'
   results.append({'symbol':symbol,'partition':part,'source':str(source),'target':str(target),'rows':len(frame),'exact_duplicates_removed':policy.exact_duplicates_removed,'conflicts_resolved':policy.conflicts_resolved,'status':status})
  activate_authoritative_route(symbol,dataset='options_v2',manifest_path='data/manifests/storage_manifest_options_v2.csv',parquet_root='data/parquet')
 out=ROOT/'research_outputs/pcs_canonical_data_repair'; out.mkdir(parents=True,exist_ok=True); (out/'options_route_promotion.json').write_text(json.dumps({'policy':'EXISTING_APPROVED_LEGACY_CANONICAL_PROMOTION','results':results,'strategy_changed':False},indent=2,default=str),encoding='utf-8'); print(json.dumps({'status':'COMPLETED','promoted':sum(x.get('status')=='PROMOTED' for x in results),'already_present':sum(x.get('status')=='ALREADY_PRESENT' for x in results)},indent=2))
if __name__=='__main__':
    from pcs.data.import_boundary import reject_legacy_import_entrypoint
    reject_legacy_import_entrypoint()
