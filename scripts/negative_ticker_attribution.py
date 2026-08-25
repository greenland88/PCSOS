from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import json,numpy as np,pandas as pd
import pcs.research.entry_candidate_universe as m
from pcs.data.access import PCSDataAccess
REPO_ROOT=Path(__file__).resolve().parents[1]
ART=REPO_ROOT/'data/parquet/research/variant_b_full'; OUT=REPO_ROOT/'data/parquet/research/negative_attribution'; OUT.mkdir(parents=True,exist_ok=True)
TICKERS=['TSLA','MU','GOOGL','META','AMZN','SPY','QQQ','AVGO','NVDA','MSFT']
def run(t):
 tr=pd.read_parquet(ART/f'{t}_full_post2020_2d.parquet'); tr=tr[tr.status.eq('COMPLETE')].copy(); tr['date']=pd.to_datetime(tr.date); tr['exit_date']=pd.to_datetime(tr.exit_date)
 d=PCSDataAccess().read_prices(t); d['atr14']=m._atr14(d); rows=[]
 for _,r in tr.iterrows():
  q=d[d.date>r.date]; path=q[q.date<=r.exit_date].head(20)
  if path.empty: continue
  entry=float(d.loc[d.date.eq(r.date),'close'].iloc[0]); atr=float(r.atr); adverse=(entry-path.low.min())/atr if atr>0 else np.nan
  z={'date':r.date,'support_state':r.support_state,'population':r.population,'pnl':r.realized_pnl,'exit_reason':r.exit_reason,'forced':r.exit_reason=='PRE_EARNINGS_EXIT','adverse_atr':adverse,'touch':float((path.low<=r.short_strike).any()),'breach':float((path.close<r.short_strike).any())}
  if r.exit_reason=='STOP':
   stopday=path[path.date.eq(r.exit_date)]
   after=d[d.date>r.exit_date].head(20)
   z.update({'days_to_stop':int((r.exit_date-r.date).days),'recovered_stop_level':float((after.close>=float(stopday.close.iloc[0])).any()) if not stopday.empty else np.nan,'recovered_entry':float((after.close>=entry).any()) if len(after) else np.nan,'finished_otm':float(after.close.iloc[-1]>r.short_strike) if len(after) else np.nan})
  rows.append(z)
 x=pd.DataFrame(rows); p=x.pnl; w=p[p>0];l=p[p<0]
 def grp(g): return {'n':len(g),'exp':round(float(g.pnl.mean()),1),'pf':round(float(g.loc[g.pnl>0,'pnl'].sum()/abs(g.loc[g.pnl<0,'pnl'].sum())),2) if (g.pnl<0).any() else None,'stop':round(float((g.exit_reason=='STOP').mean()),3),'forced':round(float(g.forced.mean()),3),'breach':round(float(g.breach.mean()),3),'adverse_p90':round(float(g.adverse_atr.quantile(.9)),2)}
 out={'ticker':t,'all':grp(x),'weak':grp(x[x.support_state=='weak']) if (x.support_state=='weak').any() else None,'moderate':grp(x[x.support_state=='moderate']) if (x.support_state=='moderate').any() else None,'stops':int((x.exit_reason=='STOP').sum()),'stop_recovered_level':round(float(x.loc[x.exit_reason=='STOP','recovered_stop_level'].mean()),3) if (x.exit_reason=='STOP').any() else None,'stop_recovered_entry':round(float(x.loc[x.exit_reason=='STOP','recovered_entry'].mean()),3) if (x.exit_reason=='STOP').any() else None,'forced_count':int(x.forced.sum())}
 (OUT/f'{t}.json').write_text(json.dumps(out,indent=2)); return out
if __name__=='__main__':
 with ProcessPoolExecutor(max_workers=8) as ex:
  fs={ex.submit(run,t):t for t in TICKERS}
  for f in as_completed(fs):
   t=fs[f]
   try: print(json.dumps(f.result()),flush=True)
   except Exception as e: print(json.dumps({'ticker':t,'error':type(e).__name__,'message':str(e)}),flush=True)
