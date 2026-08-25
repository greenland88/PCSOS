from pathlib import Path
import duckdb

symbols = ["SPY", "QQQ", "NVDA", "AMZN"]
src = Path("data/parquet/options")
dst = Path("data/parquet/options_monthly")
dst.mkdir(parents=True, exist_ok=True)
con = duckdb.connect()
for symbol in symbols:
    pattern = str(src / f"symbol={symbol}" / "**" / "*.parquet").replace("\\", "/")
    out = str(dst).replace("\\", "/")
    con.execute(f"""
        COPY (
            SELECT
                upper(CAST(symbol AS VARCHAR)) AS symbol,
                CAST(trade_date AS DATE) AS trade_date,
                CAST(expiration_date AS DATE) AS expiration,
                CAST(date_diff('day', trade_date, expiration_date) AS SMALLINT) AS DTE,
                CAST(lower(call_put) AS VARCHAR) AS option_type,
                CAST(strike AS DOUBLE) AS strike,
                CAST(bid AS DOUBLE) AS bid,
                CAST(ask AS DOUBLE) AS ask,
                CAST(last AS DOUBLE) AS last,
                CAST(volume AS INTEGER) AS volume,
                CAST(open_interest AS INTEGER) AS open_interest,
                CAST(bid_iv AS REAL) AS bid_iv,
                CAST(ask_iv AS REAL) AS ask_iv,
                CAST(delta AS REAL) AS delta,
                year(trade_date) AS trade_year,
                month(trade_date) AS trade_month
            FROM read_parquet('{pattern}', union_by_name=true)
        ) TO '{out}' (FORMAT PARQUET, PARTITION_BY (symbol, trade_year, trade_month), COMPRESSION ZSTD, OVERWRITE_OR_IGNORE)
    """)
con.close()
