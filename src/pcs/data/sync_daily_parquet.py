import argparse
from pathlib import Path
from .parquet_store import read_daily_source, write_daily_partition


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--symbol',required=True);p.add_argument('--raw-root',default='data/raw/daily_forward_adjusted');p.add_argument('--output-root',default='data/parquet/daily');a=p.parse_args(argv); paths=write_daily_partition(Path(a.raw_root)/f'{a.symbol.upper()}_daily_qfq.csv',a.symbol,a.output_root); print({'symbol':a.symbol.upper(),'partitions':len(paths),'rows_written':sum(n for _,n in paths),'status':'SUCCESS'})

if __name__=='__main__':
    from .import_boundary import reject_legacy_import_entrypoint
    reject_legacy_import_entrypoint()
