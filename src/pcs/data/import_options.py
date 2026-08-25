import argparse, time, csv
from pathlib import Path
from .parquet_store import write_option_partition, read_option_source
from .storage_manifest import append_manifest, now_utc
from .storage_schema import OPTIONS_SCHEMA_VERSION


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--symbol',required=True);p.add_argument('--year',type=int,required=True);p.add_argument('--quarter',type=int,required=True);p.add_argument('--raw-root',default='data/raw/options');p.add_argument('--output-root',default='data/parquet/options');p.add_argument('--manifest',default='data/manifests/storage_manifest.csv');a=p.parse_args(argv); source=Path(a.raw_root)/a.symbol.upper()/f'{a.symbol.upper()}_{a.year}_q{a.quarter}_option_chain.csv'; started=time.perf_counter(); path=Path(a.output_root)/f'symbol={a.symbol.upper()}'/f'year={a.year}'/f'quarter={a.quarter}'/f'{a.symbol.upper()}_{a.year}_q{a.quarter}.parquet'; raw=source.stat()
    if path.exists() and Path(a.manifest).exists():
        with Path(a.manifest).open(encoding='utf-8') as f:
            previous=list(csv.DictReader(f))
        if any(r.get('source_file')==str(source) and r.get('source_size')==str(raw.st_size) and r.get('status')=='SUCCESS' for r in previous):
            print({'symbol':a.symbol.upper(),'source_file':str(source),'parquet_path':str(path),'elapsed_seconds':time.perf_counter()-started,'status':'SKIP'}); return
    path,rows=write_option_partition(source,a.symbol,a.output_root,a.year,a.quarter); raw=source.stat(); df=read_option_source(source,a.symbol); append_manifest(a.manifest,{'dataset':'options','symbol':a.symbol.upper(),'source_file':str(source),'source_size':raw.st_size,'source_modified_time':raw.st_mtime,'row_count':rows,'min_date':df.trade_date.min(),'max_date':df.trade_date.max(),'year':a.year,'quarter':a.quarter,'parquet_path':str(path),'schema_version':OPTIONS_SCHEMA_VERSION,'import_timestamp':now_utc(),'status':'SUCCESS'}); print({'symbol':a.symbol.upper(),'source_file':str(source),'year':a.year,'quarter':a.quarter,'rows_read':len(df),'rows_written':rows,'parquet_path':str(path),'elapsed_seconds':time.perf_counter()-started,'status':'SUCCESS'})

if __name__=='__main__': main()
