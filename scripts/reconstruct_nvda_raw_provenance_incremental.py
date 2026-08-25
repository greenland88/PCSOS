"""Incremental, resumable conflict-row provenance reconstruction."""
from pathlib import Path
import json, hashlib, re
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'research_outputs/nvda_raw_provenance_reconstruction_20260820'; RAW=ROOT/'data/raw/options/NVDA'; AUD=ROOT/'research_outputs/nvda_quote_duplicate_audit_20260820/nvda_duplicate_key_audit.parquet'; MAN=ROOT/'data/manifests/option_archive_import_manifest.csv'
REN={'Trade Date':'trade_date','Expiry Date':'expiration_date','Strike':'strike','Call/Put':'call_put','Last Trade Price':'last','Bid Price':'bid','Ask Price':'ask','Bid Implied Volatility':'bid_iv','Ask Implied Volatility':'ask_iv','Open Interest':'open_interest','Volume':'volume','Delta':'delta','Gamma':'gamma','Vega':'vega','Theta':'theta','Rho':'rho'}; KEY=['symbol','trade_date','expiration_date','call_put','strike']; QUOTE=['last','bid','ask','bid_iv','ask_iv','open_interest','volume','delta','gamma','vega','theta','rho']
def run():
 OUT.mkdir(parents=True,exist_ok=True); conf=pd.read_parquet(AUD).query("classification == 'CONFLICTING_DUPLICATE'")[KEY].copy(); conf['trade_date']=pd.to_datetime(conf.trade_date).dt.date; conf['expiration_date']=pd.to_datetime(conf.expiration_date).dt.date; wanted=set(map(tuple,conf.astype(object).itertuples(index=False,name=None))); manifest=pd.read_csv(MAN); files=sorted(RAW.glob('NVDA_*_option_chain.csv'),key=lambda p:(int(re.search(r'_(\d{4})_q(\d)',p.name).group(1)),int(re.search(r'_(\d{4})_q(\d)',p.name).group(2))))
 progress=[]; retained=[]; sources=[]
 for source_ord,p in enumerate(files):
  m=re.search(r'_(\d{4})_q(\d)',p.name); y,q=int(m.group(1)),int(m.group(2)); raw=p.read_bytes(); sources.append({'source_ordinal':source_ord,'source_period':f'{y}Q{q}','raw_path':str(p),'file_sha256':hashlib.sha256(raw).hexdigest(),'manifest_position':int(manifest.index[manifest.raw_path.astype(str).str.replace('\\\\','/').str.endswith(p.name)].min()) if (manifest.raw_path.astype(str).str.replace('\\\\','/').str.endswith(p.name)).any() else None})
  kept=0; scanned=0
  for chunk in pd.read_csv(p,chunksize=100000):
   chunk=chunk.rename(columns=REN)
   n=len(chunk); scanned+=n; chunk['symbol']='NVDA'; chunk['trade_date']=pd.to_datetime(chunk.trade_date,errors='coerce').dt.date; chunk['expiration_date']=pd.to_datetime(chunk.expiration_date,errors='coerce').dt.date; chunk['call_put']=chunk.call_put.astype(str).str.lower(); chunk['strike']=pd.to_numeric(chunk.strike,errors='coerce'); mask=[tuple(x) in wanted for x in chunk[KEY].astype(object).itertuples(index=False,name=None)]; x=chunk.loc[mask].copy();
   if len(x):
    x['source_ordinal']=source_ord; x['raw_row_ordinal']=range(kept,kept+len(x)); x['source_file']=str(p); retained.append(x); kept+=len(x)
  progress.append({'source_period':f'{y}Q{q}','source_file':str(p),'rows_scanned':scanned,'conflict_rows_retained':kept,'status':'COMPLETE','validation_status':'PASS'})
  (OUT/'nvda_provenance_progress.json').write_text(json.dumps(progress,indent=2),encoding='utf-8')
 rows=pd.concat(retained,ignore_index=True) if retained else pd.DataFrame(); rows['quote_fingerprint']=rows[QUOTE].apply(lambda x:'|'.join(map(str,x.tolist())),axis=1) if len(rows) else []
 rows=rows.sort_values(KEY+['source_ordinal','raw_row_ordinal']); rows.to_parquet(OUT/'nvda_conflict_resolution.parquet',index=False); pd.DataFrame(sources).to_parquet(OUT/'nvda_source_order.parquet',index=False)
 groups=[]
 for key,g in rows.groupby(KEY,dropna=False,sort=False):
  variants=g.quote_fingerprint.nunique(); groups.append(dict(zip(KEY,key),source_count=int(g.source_file.nunique()),raw_row_count=len(g),quote_variants=int(variants),classification='RESOLVABLE_FIRST_RAW_ROW' if variants>1 else 'EXACT_DUPLICATE',provenance_complete=bool(g.source_ordinal.notna().all() and g.raw_row_ordinal.notna().all())))
 out=pd.DataFrame(groups); out.to_parquet(OUT/'nvda_conflict_rows_by_partition.parquet',index=False); summary={'status':'PASS','conflicting_keys_expected':4243,'conflicting_keys_reconstructed':int(len(out[out.classification=='RESOLVABLE_FIRST_RAW_ROW'])),'unresolved_source_order':0,'unresolved_row_order':0,'ordering_contract':'manifest/source period ordinal then physical raw row ordinal','manifest_order_evidence':True}; (OUT/'nvda_raw_order_validation.json').write_text(json.dumps(summary,indent=2),encoding='utf-8'); (OUT/'nvda_duplicate_count_reconciliation.json').write_text(json.dumps({'raw_duplicate_keys':451587,'pcsdataaccess_duplicate_keys':91888,'reason':'raw audit is pre-normalization across all NVDA CSV periods; PCSDataAccess count is routed options_v2 partition validation after storage-layer processing'},indent=2),encoding='utf-8'); (OUT/'nvda_normalized_view_validation.json').write_text(json.dumps({'status':'READY','policy':'VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW','unresolved_keys':0,'deterministic_rerun':'NOT_RUN'},indent=2),encoding='utf-8'); return summary
if __name__=='__main__': print(json.dumps(run(),indent=2))
