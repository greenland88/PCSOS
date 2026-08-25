from pathlib import Path
import pandas as pd
from pcs.research.credit_stop import run_backtest
from pcs.data.access import PCSDataAccess

ROOT=Path(__file__).resolve().parents[1]
WINDOWS={'SPY':('2020-01-02','2026-07-31'),'QQQ':('2020-01-01','2026-07-31'),'NVDA':('2024-06-10','2026-07-31'),'AMZN':('2022-06-06','2026-07-31')}
def stock(s,end):
 x=PCSDataAccess().read_prices(s,end=end).sort_values('date').drop_duplicates('date').reset_index(drop=True); x.date=pd.to_datetime(x.date).dt.normalize(); return x[x.date<=pd.Timestamp(end)].reset_index(drop=True)
def main():
 for atr in (2.0,2.5,3.0):
  frames=[]
  for s,(a,b) in WINDOWS.items():
   r=run_backtest(stock(s,b),stock('QQQ',b),option_root=f'data/parquet/options_monthly/{s}',start=a,end=b,backend='canonical',safe_strike_atr=atr)
   frames.append(pd.DataFrame([{**{k:v for k,v in t.items() if k!='events'},'ticker':s,'target_buffer_atr':atr} for t in r['trades']]))
  out=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame(); out.to_parquet(ROOT/f'research_outputs/safe_strike_{atr:.1f}ATR_candidates.parquet',index=False); print(atr,len(out))
if __name__=='__main__': main()
