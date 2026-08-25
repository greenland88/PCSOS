import time
import tracemalloc
import duckdb
from pathlib import Path

ROOT = str(Path("data/parquet/options_monthly").resolve()).replace("\\", "/")
con = duckdb.connect()
for label, start, end in [("1_day", "2026-07-01", "2026-07-01"), ("1_month", "2026-07-01", "2026-07-31"), ("1_year", "2026-01-01", "2026-07-31")]:
    symbol = "SPY"
    glob = f"{ROOT}/symbol={symbol}/trade_year=*/trade_month=*/*.parquet"
    tracemalloc.start()
    t0 = time.perf_counter()
    rows = con.execute("""
        SELECT trade_date, expiration, DTE, option_type, strike, bid, ask, last,
               volume, open_interest, bid_iv, ask_iv, delta
        FROM read_parquet(?, hive_partitioning=true)
        WHERE trade_date BETWEEN ? AND ? AND option_type='p' AND DTE BETWEEN 20 AND 45
    """, [glob, start, end]).fetchall()
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    print({"window": label, "rows_scanned": len(rows), "query_time_seconds": round(elapsed, 6), "peak_memory_bytes": peak})
con.close()
