from pathlib import Path
import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

base = Path('research_outputs/safe_strike_process_isolated/unified_four_ticker_option_qualified.parquet')
tmp = base.with_suffix('.tmp.parquet')
opt = 'data/parquet/options_monthly/symbol=*/trade_year=*/trade_month=*/*.parquet'
con = duckdb.connect()
trades = pd.read_parquet(base)
con.register('qualified_trades', trades)
source = "(SELECT DISTINCT symbol, trade_date, expiration, option_type, strike, delta FROM read_parquet(?) WHERE lower(option_type)='p')"
dup = con.execute("""
SELECT COUNT(*) FROM (
 SELECT t.ticker, t.entry_date, t.expiration, t.short_strike
 FROM qualified_trades t JOIN {src} o
 ON upper(t.ticker)=upper(o.symbol) AND CAST(t.entry_date AS DATE)=o.trade_date
 AND CAST(t.expiration AS DATE)=o.expiration AND lower(o.option_type)='p'
 AND CAST(t.short_strike AS DOUBLE)=o.strike
 GROUP BY ALL HAVING COUNT(*) > 1
)
""".format(src=source), [opt]).fetchone()[0]
if dup:
    print(con.execute("""SELECT t.ticker,t.entry_date,t.expiration,t.short_strike,COUNT(*) n,COUNT(DISTINCT o.delta) distinct_delta FROM qualified_trades t JOIN {src} o ON upper(t.ticker)=upper(o.symbol) AND CAST(t.entry_date AS DATE)=o.trade_date AND CAST(t.expiration AS DATE)=o.expiration AND lower(o.option_type)='p' AND CAST(t.short_strike AS DOUBLE)=o.strike GROUP BY ALL HAVING COUNT(*)>1""".format(src=source), [opt]).fetchdf().to_string(index=False))
    raise RuntimeError(f'non-unique selected lookup keys: {dup}')
joined = con.execute("""
SELECT t.*, o.delta AS source_short_delta
FROM read_parquet(?) t
LEFT JOIN {src} o
 ON upper(t.ticker)=upper(o.symbol)
AND CAST(t.entry_date AS DATE)=o.trade_date
AND CAST(t.expiration AS DATE)=o.expiration
AND lower(o.option_type)='p'
AND CAST(t.short_strike AS DOUBLE)=o.strike
""".format(src=source), [str(base), opt]).fetchdf()
matches = int(joined.source_short_delta.notna().sum())
no_match = int(joined.source_short_delta.isna().sum())
joined['short_delta'] = joined.pop('source_short_delta')
if len(joined) != 2165:
    raise RuntimeError(f'row count changed: {len(joined)}')
joined.to_parquet(tmp, index=False)
pq.ParquetFile(tmp)
tmp.replace(base)
print({'rows': len(joined), 'matched_exactly': matches, 'no_matching_short_leg': no_match, 'multiple_matching_short_legs': dup, 'source_delta_null': no_match})
