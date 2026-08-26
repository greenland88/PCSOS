"""Deterministic historical vendor conflict repair; never consults ClickHouse."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.storage_schema import OPTION_FIELDS
from scripts.safe_rebuild_exact_duplicates import _load, KEY

POLICY = "VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW"

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def repair(symbol):
    raw_path=Path('data/raw/options')/symbol/f'{symbol}_2025_q3_option_chain.csv'; raw=_load(raw_path,symbol)
    exact_removed=len(raw)-len(raw.drop_duplicates(OPTION_FIELDS,keep='first'))
    exact=raw.drop_duplicates(OPTION_FIELDS,keep='first')
    key_sizes=exact.groupby(KEY,dropna=False,sort=False).size()
    conflicts=int((key_sizes>1).sum())
    out=exact.drop_duplicates(KEY,keep='first').sort_values(KEY,kind='mergesort').reset_index(drop=True)
    if out[KEY].duplicated().any(): raise RuntimeError(f'{symbol}: duplicate identity keys remain')
    if any(len(g)>1 and g[OPTION_FIELDS].drop_duplicates().shape[0]>1 for _,g in out.groupby(KEY,dropna=False)): raise RuntimeError(f'{symbol}: conflicts remain')
    target=Path('data/parquet/options_v2')/f'symbol={symbol}'/'year=2025'/'quarter=3'/f'{symbol}_2025_q3.parquet'
    access=PCSDataAccess(manifest_path='data/manifests/storage_manifest_options_v2.csv',parquet_root='data/parquet')
    source_version=f'historical-vendor:{POLICY}:2025-Q3'
    access.write_partition(out,'options_v2',symbol,'year=2025/quarter=3',source_version=source_version,filename=target.name,update_manifest=False,allow_overwrite=True)
    access.update_manifest('options_v2',symbol,out,target,source_version,'year=2025/quarter=3',replace_existing=True)
    provenance={'source':'purchased historical vendor TXT/ZIP export','raw_file_path':str(raw_path),'raw_sha256':sha(raw_path),'dataset':'options_v2','symbol':symbol,'quarter':'2025 Q3','raw_rows':len(raw),'exact_duplicate_rows_removed':exact_removed,'conflicting_identity_keys':conflicts,'resolution_policy':POLICY,'clickhouse_used':False,'final_quarter_checksum':sha(target),'final_rows':len(out),'final_duplicate_identity_keys':0,'final_conflicting_keys':0,'status':'PROMOTED'}
    access.record_provenance(provenance)
    return provenance

if __name__=='__main__':
    from pcs.data.import_boundary import reject_legacy_import_entrypoint
    reject_legacy_import_entrypoint()
