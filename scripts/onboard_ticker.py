"""Run one real historical TXT -> ClickHouse overlap -> options_v2 onboarding."""
from __future__ import annotations

import argparse
import calendar
import os
import sys
import tempfile
from pathlib import Path

import pyarrow.parquet as pq

from pcs.data.access import PCSDataAccess
from pcs.data.onboarding import HistoricalTxtZipAdapter, onboard_ticker
from pcs.data.storage_schema import OPTION_FIELDS
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_clickhouse_options import MAP, SOURCE, _load_dotenv, _query


def run(symbol: str, year: int, quarter: int, *, archive_root: str, root: str, manifest: str, provenance: str, host: str, user: str | None, password: str | None):
    _load_dotenv(Path(".env"))
    user = user or os.getenv("CLICKHOUSE_USER", "hisdata230")
    password = password or os.getenv("CLICKHOUSE_PASSWORD")
    if not password:
        raise RuntimeError("CLICKHOUSE_PASSWORD is required")
    adapter = HistoricalTxtZipAdapter(archive_root)
    start = f"{year:04d}-{(quarter - 1) * 3 + 1:02d}-01"
    end_month = quarter * 3
    end = f"{year:04d}-{end_month:02d}-{calendar.monthrange(year, end_month)[1]:02d}"
    safe_symbol = symbol.upper().replace("'", "''")
    select = ", ".join(f"{source} AS {dest}" for source, dest in MAP.items())
    sql = (f"SELECT {select} FROM {SOURCE} WHERE Symbol IN ('{safe_symbol}') "
           f"AND TradeDate BETWEEN '{start}' AND '{end}' ORDER BY TradeDate, Symbol, ExpiryDate, Strike FORMAT Parquet")

    def loader(requested_symbol: str, _year: int, _quarter: int):
        with tempfile.TemporaryDirectory(prefix="pcs_onboard_clickhouse_") as directory:
            path = Path(directory) / "clickhouse.parquet"
            _query(host, user, password, sql, path)
            return pq.ParquetFile(path).read().to_pandas()[OPTION_FIELDS]

    access = PCSDataAccess(manifest_path=manifest, parquet_root=root)
    return onboard_ticker(symbol, [(year, quarter)], loader, adapter=adapter, access=access, dataset="options_v2")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("year", type=int)
    parser.add_argument("quarter", type=int)
    parser.add_argument("--archive-root", default=r"K:\BaiduNetdiskDownload\USDailyOptions")
    parser.add_argument("--root", default="data/parquet")
    parser.add_argument("--manifest", default="data/manifests/storage_manifest_options_v2.csv")
    parser.add_argument("--provenance", default="data/manifests/data_provenance_manifest.csv")
    parser.add_argument("--host", default="http://db.base32.cn:8123/")
    parser.add_argument("--user")
    parser.add_argument("--password")
    args = parser.parse_args()
    result = run(args.symbol, args.year, args.quarter, archive_root=args.archive_root, root=args.root,
                 manifest=args.manifest, provenance=args.provenance, host=args.host, user=args.user, password=args.password)
    print(result.to_dict())
    return 0 if result.status == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
