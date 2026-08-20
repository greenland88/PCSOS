from pathlib import Path
import duckdb, pandas as pd, pyarrow as pa, pyarrow.parquet as pq, os

opt='data/parquet/options_monthly/symbol=*/trade_year=*/trade_month=*/*.parquet'
base=Path('research_outputs/safe_strike_process_isolated/unified_four_ticker_option_qualified.parquet')
c=duckdb.connect()
q="""SELECT *, filename FROM read_parquet(?, hive_partitioning=true) WHERE upper(symbol)='QQQ' AND trade_date=DATE '2025-07-03' AND expiration=DATE '2025-08-01' AND lower(option_type)='p' AND strike=540"""
print(c.execute(q,[opt]).fetchdf().to_string(index=False))
trades=pd.read_parquet(base)
t=trades[(trades.ticker=='QQQ')&(pd.to_datetime(trades.entry_date)==pd.Timestamp('2025-07-03'))&(pd.to_datetime(trades.expiration)==pd.Timestamp('2025-08-01'))&(trades.short_strike==540)]
print('TRADE_ROW'); print(t.to_string(index=False))
src="(SELECT symbol,trade_date,expiration,option_type,strike,MAX(delta) AS delta,COUNT(*) AS source_matches,COUNT(DISTINCT delta) AS distinct_deltas FROM read_parquet(?) WHERE lower(option_type)='p' GROUP BY ALL)"
c.register('qualified_trades',trades)
joined=c.execute("""SELECT t.*,CASE WHEN o.distinct_deltas=1 THEN o.delta ELSE NULL END AS source_short_delta,o.source_matches,o.distinct_deltas FROM read_parquet(?) t LEFT JOIN {src} o ON upper(t.ticker)=upper(o.symbol) AND CAST(t.entry_date AS DATE)=o.trade_date AND CAST(t.expiration AS DATE)=o.expiration AND lower(o.option_type)='p' AND CAST(t.short_strike AS DOUBLE)=o.strike""".format(src=src),[str(base),opt]).fetchdf()
joined['short_delta']=joined.pop('source_short_delta')
joined.loc[joined['distinct_deltas'].eq(1),'short_delta']=joined.loc[joined['distinct_deltas'].eq(1),'source_short_delta'] if 'source_short_delta' in joined else joined.loc[joined['distinct_deltas'].eq(1),'short_delta']
joined['delta_resolution_status']=joined.apply(lambda r:'SOURCE_AMBIGUITY' if pd.notna(r['distinct_deltas']) and r['distinct_deltas']>1 else 'RESOLVED' if pd.notna(r['distinct_deltas']) else 'NO_MATCH',axis=1)
joined=joined.drop(columns=['source_matches','distinct_deltas'])
print('COUNTS',joined.delta_resolution_status.value_counts().to_dict())
tmp=base.with_suffix('.tmp.parquet'); joined.to_parquet(tmp,index=False); os.replace(tmp,base)
print('ROWS',len(joined))
