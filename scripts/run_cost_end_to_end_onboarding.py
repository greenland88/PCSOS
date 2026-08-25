"""Research-only COST new-ticker onboarding acceptance run."""
from __future__ import annotations
import calendar, os, tempfile
from pathlib import Path
import pyarrow.parquet as pq
from pcs.data.access import PCSDataAccess
from pcs.data.onboarding import HistoricalTxtZipAdapter, onboard_ticker_incremental
from pcs.data.storage_schema import OPTION_FIELDS
from sync_clickhouse_options import MAP, SOURCE, _load_dotenv, _query

SYMBOL = "COST"
ARCHIVE = r"K:\BaiduNetdiskDownload\USDailyOptions"
PERIODS = [(y, q) for y in range(2010, 2027) for q in range(1, 5) if not (y == 2026 and q > 3)]

def main():
    _load_dotenv(Path(".env"))
    host = os.getenv("CLICKHOUSE_HOST", "http://db.base32.cn:8123/")
    user = os.getenv("CLICKHOUSE_USER", "hisdata230")
    password = os.getenv("CLICKHOUSE_PASSWORD")
    if not password:
        raise RuntimeError("CLICKHOUSE_PASSWORD is required")
    adapter = HistoricalTxtZipAdapter(ARCHIVE)
    access = PCSDataAccess(manifest_path="data/manifests/storage_manifest_options_v2.csv", parquet_root="data/parquet")
    def loader(symbol: str, year: int, quarter: int):
        start = f"{year:04d}-{(quarter-1)*3+1:02d}-01"; month = quarter*3
        end = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
        safe = symbol.replace("'", "''")
        select = ", ".join(f"{s} AS {d}" for s, d in MAP.items())
        sql = f"SELECT {select} FROM {SOURCE} WHERE Symbol IN ('{safe}') AND TradeDate BETWEEN '{start}' AND '{end}' ORDER BY TradeDate, Symbol, ExpiryDate, Strike FORMAT Parquet"
        with tempfile.TemporaryDirectory(prefix="pcs_cost_clickhouse_") as td:
            path = Path(td) / "q.parquet"; _query(host, user, password, sql, path)
            return pq.ParquetFile(path).read().to_pandas()[OPTION_FIELDS]
    result = onboard_ticker_incremental(SYMBOL, PERIODS, loader, adapter=adapter, access=access, dataset="options_v2", workers=int(os.getenv("PCS_ONBOARD_WORKERS", "8")), resume=True)
    print(result.to_dict())
    return 0 if result.status == "READY" else 1
if __name__ == "__main__": raise SystemExit(main())
