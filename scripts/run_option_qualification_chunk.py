from __future__ import annotations
import argparse, json, os, time
from pathlib import Path
import pandas as pd
from pcs.research.credit_stop import run_backtest
from pcs.data.access import PCSDataAccess

def load_daily(symbol, end):
    x=PCSDataAccess().read_prices(symbol, end=end).sort_values('date').drop_duplicates('date')
    x.date=pd.to_datetime(x.date).dt.normalize(); return x[x.date<=pd.Timestamp(end)]

def main():
    p=argparse.ArgumentParser(); p.add_argument('--ticker',required=True); p.add_argument('--start',required=True); p.add_argument('--end',required=True); p.add_argument('--output',required=True); args=p.parse_args()
    t0=time.perf_counter(); symbol=args.ticker.upper(); stock=load_daily(symbol,args.end); bench=load_daily('QQQ',args.end)
    result=run_backtest(stock,bench,option_root=f'data/parquet/options_monthly/{symbol}',start=args.start,end=args.end,backend='canonical')
    rows=[]
    for t in result['trades']:
        r={k:v for k,v in t.items() if k!='events'}; r.update(ticker=symbol,entry_date=t['date'],spot=t['close'],ATR=t['atr14'],**{'Trend Gate':t.get('trend_gate')},DTE=(pd.Timestamp(t['expiration'])-pd.Timestamp(t['date'])).days,width=t['short_strike']-t['long_strike'],credit=t.get('initial_credit'),short_delta=None); rows.append(r)
    out=pd.DataFrame(rows); required=['ticker','entry_date','spot','ATR','expiration','DTE','short_strike','long_strike','width','credit']
    for c in required:
        if c not in out: out[c] = pd.Series(dtype='object')
    tmp=Path(args.output).with_suffix('.tmp.parquet'); out.to_parquet(tmp,index=False); pd.read_parquet(tmp); os.replace(tmp,args.output)
    meta={'ticker':symbol,'start':args.start,'end':args.end,'option_rows_loaded':result['quality'].get('option_rows_loaded',0),'qualified_trades':len(out),'runtime_seconds':time.perf_counter()-t0,'peak_rss_bytes':None,'output_path':str(Path(args.output).resolve())}
    Path(args.output+'.json').write_text(json.dumps(meta,default=str),encoding='utf-8'); print(json.dumps(meta),flush=True)
if __name__=='__main__': main()
