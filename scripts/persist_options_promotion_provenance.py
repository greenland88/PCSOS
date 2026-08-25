from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]

def main():
    path=ROOT/'data/manifests/data_provenance_manifest.csv'
    d=pd.read_csv(path)
    m=pd.read_csv(ROOT/'data/manifests/storage_manifest_options_v2.csv')
    existing=set(d.provenance_key.astype(str))
    rows=[]
    for _,r in m[m.source_file.astype(str).str.startswith('legacy-promotion:')].iterrows():
        key=f"options_v2|{r.symbol}|{int(r.year)}|{int(r.quarter)}|legacy-promotion"
        if key in existing: continue
        rec={c:'' for c in d.columns}
        rec.update({'source':'legacy_canonical_promotion','source_table':'options','symbol':r.symbol,'source_version':f'legacy-promotion:{r.source_file}','dataset':'options_v2','status':'PROMOTED','source_path':r.source_file,'source_file':r.source_file,'parquet_path':r.parquet_path,'year':int(r.year),'quarter':int(r.quarter),'rows':int(r.row_count),'canonical_rows':int(r.row_count),'final_rows':int(r.row_count),'route':'options_v2','resolution_policy':'EXISTING_APPROVED_CANONICAL_PROMOTION','authoritative_source':'approved_vendor_manifest','provenance_key':key,'partition':f"year={int(r.year)}/quarter={int(r.quarter)}"})
        rows.append(rec)
    if rows:
        pd.concat([d,pd.DataFrame(rows)],ignore_index=True).to_csv(path,index=False)
    print({'status':'COMPLETED','records_added':len(rows)})

if __name__=='__main__': main()
