from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import json, numpy as np, pandas as pd

REPO_ROOT=Path(__file__).resolve().parents[1]
ART=REPO_ROOT/'data/parquet/research/variant_b_full'; RISK=REPO_ROOT/'data/parquet/research/premium_risk'; OUT=REPO_ROOT/'data/parquet/research/pit_compensation'; OUT.mkdir(parents=True,exist_ok=True)
TICKERS=['AAPL','AMD','AMZN','AVGO','CRM','GOOGL','HOOD','META','MSFT','MU','NFLX','NVDA','QQQ','SPY','TSLA','VRT']
MIN_COND=30; MIN_TICKER=30

def summarize(x):
 p=x.pnl; w=p[p>0]; l=p[p<0]
 return {'n':len(x),'expectancy':float(p.mean()),'pf':float(w.sum()/abs(l.sum())) if len(l) else None,'win_rate':float((p>0).mean()),'stop_rate':float((x.exit_reason=='STOP').mean()),'worst':float(p.min()),'p10':float(p.quantile(.1)),'p5':float(p.quantile(.05))}

def run(t):
 x=pd.read_parquet(RISK/f'{t}_trades.parquet'); x['date']=pd.to_datetime(x.date); x=x.sort_values(['date']).reset_index(drop=True)
 vals=[]
 for i,r in x.iterrows():
  past=x.iloc[:i]
  cond=past[past.support_state.eq(r.support_state)]
  if len(cond)>=MIN_COND: hist=cond; level='support'
  elif len(past)>=MIN_TICKER: hist=past; level='ticker'
  else: hist=past; level='insufficient'
  if len(hist):
   med=hist.adverse_atr.median(); p75=hist.adverse_atr.quantile(.75); p90=hist.adverse_atr.quantile(.9); p95=hist.adverse_atr.quantile(.95); touch=hist.strike_touch.mean(); breach=hist.strike_breach.mean()
  else: med=p75=p90=p95=touch=breach=np.nan
  vals.append({'pit_sample':len(hist),'pit_level':level,'pit_median_adverse_atr':med,'pit_p75_adverse_atr':p75,'pit_p90_adverse_atr':p90,'pit_p95_adverse_atr':p95,'pit_touch_prob':touch,'pit_breach_prob':breach})
 z=pd.concat([x.reset_index(drop=True),pd.DataFrame(vals)],axis=1)
 for col in ['pit_median_adverse_atr','pit_p75_adverse_atr','pit_p90_adverse_atr','pit_p95_adverse_atr']:
  z['credit_over_'+col.replace('pit_','')]=z.credit/z[col].replace(0,np.nan)
 z['pit_compensation']=z['credit_over_p90_adverse_atr']; valid=z.pit_compensation.replace([np.inf,-np.inf],np.nan).notna(); z['comp_q']=np.nan; z.loc[valid,'comp_q']=pd.qcut(z.loc[valid,'pit_compensation'],4,labels=False,duplicates='drop')+1
 groups=[]
 for q,g in z[z.comp_q.notna()].groupby('comp_q'):
  groups.append({'quartile':int(q),'pit_sample_median':float(g.pit_sample.median()),**summarize(g)})
 out={'ticker':t,'n':len(z),'pit_valid':int(valid.sum()),'pit_levels':z.pit_level.value_counts().to_dict(),'quartiles':groups,'conditional_min_history':MIN_COND}
 z.to_parquet(OUT/f'{t}_pit.parquet',index=False); (OUT/f'{t}_summary.json').write_text(json.dumps(out,indent=2)); return out

if __name__=='__main__':
 with ProcessPoolExecutor(max_workers=8) as ex:
  fs={ex.submit(run,t):t for t in TICKERS}
  for f in as_completed(fs):
   t=fs[f]
   try: print(json.dumps(f.result()),flush=True)
   except Exception as e: print(json.dumps({'ticker':t,'error':type(e).__name__,'message':str(e)}),flush=True)
