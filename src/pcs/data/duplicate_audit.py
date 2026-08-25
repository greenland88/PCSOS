"""Canonical options duplicate/conflict audit; read-only until explicitly repaired."""
from __future__ import annotations
from pathlib import Path
import json
import pandas as pd

KEY = ['trade_date','expiration_date','call_put','strike']

def _audit_frame(frame: pd.DataFrame, path: str) -> dict:
    d=frame.copy()
    for c in ('trade_date','expiration_date'):
        if c in d: d[c]=pd.to_datetime(d[c]).dt.strftime('%Y-%m-%d')
    key=KEY
    dup=d[d.duplicated(key,False)].copy()
    if dup.empty: return {'path':path,'rows':len(d),'duplicate_rows':0,'duplicate_keys':0,'exact_duplicate_keys':0,'conflicting_keys':0,'examples':[]}
    payload=[c for c in d.columns if c not in key]
    dup['_payload_hash']=pd.util.hash_pandas_object(dup[payload].astype(object),index=False).astype('uint64')
    grouped=dup.groupby(key,dropna=False,sort=False)
    versions=grouped['_payload_hash'].nunique()
    examples=[]
    for idx in versions.index[:3]:
        rows=dup.set_index(key).loc[idx].reset_index()
        examples.append({'key':{k:(v.isoformat() if hasattr(v,'isoformat') else v) for k,v in zip(key,idx)},'rows':rows.drop(columns=['_payload_hash']).head(4).to_dict('records')})
    return {'path':path,'rows':len(d),'duplicate_rows':len(dup),'duplicate_keys':len(versions),'exact_duplicate_keys':int((versions==1).sum()),'conflicting_keys':int((versions>1).sum()),'examples':examples}

def audit(root='data/parquet/options_v2', manifest='data/manifests/storage_manifest_v2.csv', symbol=None, year=None):
    root=Path(root); records=[]; frames={}
    for p in sorted(root.glob('symbol=*/year=*/quarter=*/*.parquet')):
        parts=p.parts; sym=next((x.split('=',1)[1] for x in parts if x.startswith('symbol=')), '')
        yr=next((x.split('=',1)[1] for x in parts if x.startswith('year=')), '')
        if symbol and sym.upper()!=symbol.upper(): continue
        if year and yr!=str(year): continue
        d=pd.read_parquet(p); records.append(_audit_frame(d,str(p))); frames[(sym,yr,p.parent.name)]=d
    m=pd.read_csv(manifest) if Path(manifest).exists() else pd.DataFrame()
    if len(m):
        msel=m[(m.symbol.astype(str).str.upper()==str(symbol).upper()) if symbol else pd.Series(True,index=m.index)]
        if year is not None: msel=msel[msel.year.astype(str)==str(year)]
        manifest_dup=int(msel.duplicated(['dataset','symbol','year','quarter'],False).sum())
        source_versions=int(msel.source_file.astype(str).nunique())
        manifest_rows=msel.to_dict('records')
        # Partition/source topology checks are intentionally independent of
        # row-level duplicate checks so ingestion and routing causes remain
        # visible even when a partition has already been repaired.
        overlap_examples=[]
        for (ds, sy, yr), group in msel.groupby(['dataset','symbol','year'], dropna=False):
            intervals=group.sort_values(['min_date','max_date']).to_dict('records')
            for left, right in zip(intervals, intervals[1:]):
                if str(right.get('min_date')) <= str(left.get('max_date')):
                    overlap_examples.append({'left':left,'right':right})
        source_duplicate_rows=int(msel.duplicated(['dataset','symbol','year','quarter','source_file'],False).sum())
        route_duplicate_rows=int(msel.duplicated(['symbol','year','quarter','parquet_path'],False).sum())
    else: manifest_dup=0; source_versions=0; manifest_rows=[]; overlap_examples=[]; source_duplicate_rows=0; route_duplicate_rows=0
    result={'module':'pcs.data.duplicate_audit','status':'COMPLETED','key':KEY,'partitions':records,'partition_duplicate_rows':sum(x['duplicate_rows'] for x in records),'partition_duplicate_keys':sum(x['duplicate_keys'] for x in records),'exact_duplicate_keys':sum(x['exact_duplicate_keys'] for x in records),'conflicting_keys':sum(x['conflicting_keys'] for x in records),'manifest_duplicate_partition_rows':manifest_dup,'manifest_source_versions':source_versions,'manifest_rows':manifest_rows,'overlapping_source_partition_count':len(overlap_examples),'overlapping_source_partition_examples':overlap_examples[:3],'duplicate_source_version_rows':source_duplicate_rows,'route_merge_duplicate_rows':route_duplicate_rows,'route':'options_v2','route_merge_duplication':'INACTIVE_ROUTE_NOT_INCLUDED' if route_duplicate_rows == 0 else 'DUPLICATE_MANIFEST_TARGET'}
    return result

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument('--symbol'); ap.add_argument('--year',type=int); ap.add_argument('--output',default='research_outputs/qqq_options_duplicate_audit.json'); a=ap.parse_args()
    out=a.output; Path(out).parent.mkdir(parents=True,exist_ok=True); Path(out).write_text(json.dumps(audit(symbol=a.symbol,year=a.year),indent=2,default=str)); print(Path(out).read_text())
