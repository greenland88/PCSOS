"""Batch A fresh R1 path-risk validation; research-only and resumable."""
from pathlib import Path
from collections import deque
import pandas as pd, numpy as np
from pcs.data.access import PCSDataAccess
from .batch_trend_history_fast import build_fast_batch_trend_history

BATCH_A='SPY SMH XLK XLF XLE IWM GLD TLT JPM BAC GS XOM CAT BA GE COST HD DIS NFLX CRM ORCL CSCO TSM MRVL LLY UNH COIN HOOD F GM'.split()
OUT=Path('research_outputs/batch_a_r1_validation'); OUT.mkdir(parents=True,exist_ok=True)
CHECK=OUT/'ticker_results.csv'; FS=['atr_expansion','drawdown20','down_streak','atr_pct','move5_atr']

def daily(s):
 d=PCSDataAccess().read_prices(s); d.date=pd.to_datetime(d.date); return d.sort_values('date').drop_duplicates('date')
def feat(d):
 d=d.copy(); p=d.close.shift(); tr=pd.concat([d.high-d.low,(d.high-p).abs(),(d.low-p).abs()],axis=1).max(axis=1); d['atr14']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); d['atr_pct']=d.atr14/d.close; d['atr_expansion']=d.atr14/d.atr14.rolling(60,min_periods=20).median(); d['drawdown20']=1-d.close/d.close.rolling(20,min_periods=5).max(); down=d.close.diff().lt(0); d['down_streak']=down.groupby((~down).cumsum()).cumsum().astype(float); d['move5_atr']=(d.close-d.close.shift(5)).abs()/d.atr14; return d
def states(d):
 h={f:deque(maxlen=756) for f in FS}; out=[]
 for _,r in d.iterrows():
  z=[float((np.asarray(h[f])<r[f]).mean()) for f in FS if pd.notna(r[f]) and len(h[f])>=50]; score=np.nan if len(z)!=5 else .67*np.mean(z[:3])+.33*np.mean(z[3:]); out.append('R1' if pd.notna(score) and score<.25 else 'non-R1' if pd.notna(score) else None)
  for f in FS:
   if pd.notna(r[f]): h[f].append(float(r[f]))
 return pd.DataFrame({'date':d.date,'r1_state':out})
def one(s, allout):
 stock=daily(s); trend,_=build_fast_batch_trend_history(stock,daily('QQQ'),symbol=s,benchmark_symbol='QQQ'); q=trend[trend.trend_gate.eq('PASS')][['date']].merge(states(feat(stock)),on='date').merge(allout[allout.ticker.eq(s)].rename(columns={'breach_5d_2atr':'b5','breach_10d_2atr':'b10'}),on='date'); q['date']=pd.to_datetime(q.date); q['year']=q.date.dt.year; q.to_csv(OUT/f'{s}_rows.csv',index=False); return q
def metrics(q):
 rows=[]
 for period,z in [('FULL',q)]+[(str(y),z) for y,z in q.groupby(q.date.dt.year)]:
  r=z[z.r1_state.eq('R1')]; n=z[~z.r1_state.eq('R1')]
  def m(x,c): return x[c].mean() if len(x) else np.nan
  rows.append({'period':period,'r1_n':len(r),'non_r1_n':len(n),'diff_5d_mae':m(r,'mae_5d_atr')-m(n,'mae_5d_atr'),'diff_10d_mae':m(r,'mae_10d_atr')-m(n,'mae_10d_atr'),'diff_5d_breach_pp':(m(r,'b5')-m(n,'b5'))*100,'diff_10d_breach_pp':(m(r,'b10')-m(n,'b10'))*100})
 return rows
def run():
 import pyarrow.dataset as ds
 allout=ds.dataset('research_outputs/r1_external_forward_outcomes_v1.parquet',format='parquet').to_table().to_pandas(); old=pd.read_csv(CHECK) if CHECK.exists() else pd.DataFrame(columns=['ticker','status','error','trend_pass_n'])
 for s in BATCH_A:
  if ((old.ticker==s)&(old.status=='COMPLETE')).any(): continue
  try:
   q=one(s,allout); pd.DataFrame(metrics(q)).assign(ticker=s).to_csv(OUT/f'{s}_metrics.csv',index=False); row={'ticker':s,'status':'COMPLETE','error':'','trend_pass_n':len(q)}
  except Exception as e: row={'ticker':s,'status':'FAILED','error':f'{type(e).__name__}: {e}','trend_pass_n':0}
  old=pd.concat([old,pd.DataFrame([row])],ignore_index=True); old.to_csv(CHECK,index=False); print(row,flush=True)
 return old
if __name__=='__main__': print(run().to_string(index=False))
