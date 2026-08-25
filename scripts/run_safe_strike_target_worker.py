import argparse, json
from pathlib import Path
import pandas as pd
from run_safe_strike_candidates import stock
from pcs.research.credit_stop import run_backtest
W={'SPY':('2020-01-02','2026-07-31'),'QQQ':('2020-01-01','2026-07-31'),'NVDA':('2024-06-10','2026-07-31'),'AMZN':('2022-06-06','2026-07-31')}
p=argparse.ArgumentParser(); p.add_argument('--atr',type=float,required=True); a=p.parse_args()
root=Path('research_outputs/safe_strike_stage1_pass_only')/f'{a.atr:.1f}ATR'; root.mkdir(parents=True,exist_ok=True)
for s,(lo,hi) in W.items():
 r=run_backtest(stock(s,hi),stock('QQQ',hi),option_root=f'data/parquet/options_monthly/{s}',start=lo,end=hi,backend='canonical',safe_strike_atr=a.atr)
 pass_trades=[t for t in r['trades'] if t.get('trend_gate')=='PASS']
 out=pd.DataFrame([{**{k:v for k,v in t.items() if k!='events'},'ticker':s,'target_buffer_atr':a.atr,'candidate_status':'TRADE_QUALIFIED'} for t in pass_trades]); out.to_parquet(root/f'{s}.parquet',index=False); print(json.dumps({'atr':a.atr,'ticker':s,'qualified':len(out),'pass_only':True}),flush=True)
