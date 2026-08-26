"""Read-only ClickHouse option pull and auditable PCSDataAccess sync."""
from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from pcs.data.access import PCSDataAccess
from pcs.data.clickhouse import PCSClickHouseClient, ClickHouseError
from pcs.data.storage_schema import OPTION_FIELDS

MIN_SYNC_DATE = date(2026, 8, 1)
SOURCE = "firstrate.options_kline_1d"
MAP = {
    "Symbol": "symbol", "TradeDate": "trade_date", "ExpiryDate": "expiration_date",
    "Strike": "strike", "CallPut": "call_put", "LastTradePrice": "last",
    "BidPrice": "bid", "AskPrice": "ask", "BidImpliedVolatilities": "bid_iv",
    "AskImpliedVolatilities": "ask_iv", "OpenInterest": "open_interest",
    "Volume": "volume", "Delta": "delta", "Gamma": "gamma", "Vega": "vega",
    "Theta": "theta", "Rho": "rho",
}
KEY = ["trade_date", "symbol", "expiration_date", "strike", "call_put"]


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding process environment."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip("'\"")


def _query(url: str, user: str, password: str, sql: str, path: Path) -> None:
    client = PCSClickHouseClient(url, user, password)
    try:
        client.query(sql, operation="select", output=path)
    except ClickHouseError as exc:
        # Preserve structured diagnostics for callers while retaining the
        # historical exception boundary used by onboarding.
        raise RuntimeError({"clickhouse_diagnostics": vars(exc.diagnostics)}) from exc


def _dedupe(frame: pd.DataFrame) -> tuple[pd.DataFrame, int, int, int]:
    """Preserve existing key/keep-last semantics and report duplicate counts."""
    physical = len(frame)
    unique_keys = int(frame[KEY].drop_duplicates().shape[0])
    all_fields = [c for c in OPTION_FIELDS if c not in KEY]
    exact = frame.drop_duplicates(subset=KEY + all_fields, keep="last")
    final = exact.drop_duplicates(KEY, keep="last").sort_values(KEY, kind="mergesort").reset_index(drop=True)
    return final[OPTION_FIELDS], physical, unique_keys, physical - len(final)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="http://db.base32.cn:8123/")
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--root", default="data/parquet/options_v2")
    parser.add_argument("--dataset", default="options_v2")
    parser.add_argument("--manifest-path", default="data/manifests/storage_manifest_options_v2.csv")
    parser.add_argument("--provenance-path", default="data/manifests/data_provenance_manifest.csv")
    parser.add_argument("--symbol", default="NVDA")
    parser.add_argument("--start", default="2026-08-01")
    parser.add_argument("--end", default=str(date.today()))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    _load_dotenv(Path(args.env_file))
    user = args.user or os.getenv("CLICKHOUSE_USER", "hisdata230")
    password = args.password or os.getenv("CLICKHOUSE_PASSWORD")
    if not password:
        parser.error("set CLICKHOUSE_PASSWORD or provide --password")
    start = max(MIN_SYNC_DATE, date.fromisoformat(args.start))
    end = date.fromisoformat(args.end)
    if start > end:
        parser.error("start must not be after end")
    symbol = str(args.symbol).strip().upper()
    access = PCSDataAccess(manifest_path=args.manifest_path, parquet_root=Path(args.root).parent)
    partition = "year=2026/quarter=3"
    # Keep the historical Q3 partition immutable.  The incremental file is
    # deliberately a separate manifest-backed file in the same logical
    # quarter; its dates begin after the existing canonical coverage.
    filename = f"{symbol}_2026_q3_incremental_{start}_{end}.parquet"
    target = Path(args.root) / f"symbol={symbol}" / partition / filename
    select = ", ".join(f"{source} AS {dest}" for source, dest in MAP.items())
    escaped = symbol.replace("'", "''")
    sql = (
        f"SELECT {select} FROM {SOURCE} WHERE Symbol IN ('{escaped}') "
        f"AND TradeDate BETWEEN '{start}' AND '{end}' "
        "ORDER BY TradeDate, Symbol, ExpiryDate, Strike FORMAT Parquet"
    )
    sync_timestamp = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="pcs_clickhouse_sync_") as temp_dir:
        source_path = Path(temp_dir) / "source.parquet"
        _query(args.host, user, password, sql, source_path)
        source_hash = _sha256(source_path)
        source_frame = pq.ParquetFile(source_path).read().to_pandas()[OPTION_FIELDS]
        source_frame = source_frame.copy()
        source_frame["trade_date"] = pd.to_datetime(source_frame["trade_date"]).dt.date
        source_frame["expiration_date"] = pd.to_datetime(source_frame["expiration_date"]).dt.date
        source_frame, physical, unique_keys, duplicates_removed = _dedupe(source_frame)
        rows_written = len(source_frame)
        source_version = f"clickhouse:{SOURCE}:{start}:{end}:sha256:{source_hash}"
        record = {
            "source": "ClickHouse", "source_table": SOURCE, "symbol": symbol,
            "query_start": str(start), "query_end": str(end),
            "sync_timestamp": sync_timestamp, "physical_rows_fetched": physical,
            "unique_keys_fetched": unique_keys, "duplicates_removed": duplicates_removed,
            "rows_written": rows_written, "checksum_sha256": source_hash,
            "source_version": source_version, "dataset": args.dataset,
            "parquet_path": str(target), "dry_run": not args.apply,
        }
        print(f"source={SOURCE}\nquery_start={start}\nquery_end={end}\nphysical_rows={physical}\nunique_keys={unique_keys}\nduplicates_removed={duplicates_removed}\nrows_written={rows_written}\nchecksum_sha256={source_hash}\ndry_run={not args.apply}")
        if not args.apply:
            return
        access.write_partition(source_frame, args.dataset, symbol, partition, source_version=source_version, allow_overwrite=False, replace_manifest=False, filename=filename)
        access.record_provenance(record, args.provenance_path)
        print(f"updated={target}")


if __name__ == "__main__":
    from pcs.data.import_boundary import reject_legacy_import_entrypoint
    reject_legacy_import_entrypoint()
