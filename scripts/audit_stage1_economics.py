from pathlib import Path
import pandas as pd, numpy as np
from pcs.data.access import PCSDataAccess

W={'SPY':('2020-01-02','2026-07-31'),'QQQ':('2020-01-01','2026-07-31'),'NVDA':('2024-06-10','2026-07-31'),'AMZN':('2022-06-06','2026-07-31')}; ats=[1.5,2.0,2.5,3.0]
ACCESS=PCSDataAccess()
def daily(s,end):
 x=ACCESS.read_prices(s, end_date=end).sort_values('date').drop_duplicates('date').reset_index(drop=True); x.date=pd.to_datetime(x.date).dt.normalize(); return x
def eligible(s,a,b):
 t=pd.read_parquet(Path('research_outputs/safe_strike_risk_map_v0_1/trend_histories')/f'{s}_trend.parquet',columns=['date','trend_gate']); t.date=pd.to_datetime(t.date); return int(t.date.between(pd.Timestamp(a),pd.Timestamp(b)).loc[t.trend_gate.eq('PASS')].sum())
def metric(x,s):
 d=daily(s,W[s][1]).set_index('date'); sessions=pd.DatetimeIndex(d.index); positions={day:i for i,day in enumerate(sessions)}; touch={}
 for h in (5,10):
  values=[]
  for _,r in x.iterrows():
   pos=positions.get(pd.Timestamp(r.date).normalize()); future=sessions[pos+1:min(pos+h,len(sessions))] if pos is not None else sessions[:0]
   values.append(bool((d.reindex(future).low<=r.short_strike).any()))
  touch[h]=np.mean(values) if values else np.nan
 pnl=pd.to_numeric(x.realized_pnl,errors='coerce'); wins=pnl[pnl>0]; losses=pnl[pnl<0]; stop=(x.exit_reason=='STOP').mean() if len(x) else np.nan; pf=wins.sum()/abs(losses.sum()) if len(losses) else np.nan; curve=pnl.fillna(0).cumsum(); dd=(curve-curve.cummax()).min() if len(curve) else np.nan
 return {'N':len(x),'buffer_mean':x.short_buffer_atr.mean(),'buffer_median':x.short_buffer_atr.median(),'buffer_p25':x.short_buffer_atr.quantile(.25),'buffer_p75':x.short_buffer_atr.quantile(.75),'credit_mean':x.credit.mean(),'credit_median':x.credit.median(),'credit_p25':x.credit.quantile(.25),'credit_p75':x.credit.quantile(.75),'credit_width_mean':x.credit_width_ratio.mean(),'credit_width_median':x.credit_width_ratio.median(),'DTE_mean':x.DTE.mean(),'DTE_median':x.DTE.median(),'width_mean':x.width.mean(),'width_median':x.width.median(),'short_delta_mean':x.short_delta.mean(),'short_delta_median':x.short_delta.median(),'short_delta_N':int(x.short_delta.notna().sum()),'touch5':touch[5],'touch10':touch[10],'stop_rate':stop,'win_rate':(pnl>0).mean(),'avg_winner':wins.mean(),'avg_loser':losses.mean(),'expectancy':pnl.mean(),'profit_factor':pf,'max_drawdown':dd,'avg_holding_days':x.days_held.mean()}
rows=[]; populations={}
for a in ats:
 for s,(lo,hi) in W.items():
  x=pd.concat([pd.read_parquet(p) for p in (Path('research_outputs/safe_strike_stage1_pass_only')/f'{a:.1f}ATR').glob(f'{s}.parquet')],ignore_index=True); x=x[x.trend_gate.eq('PASS')].copy(); x['entry_date']=pd.to_datetime(x.date); x['credit']=x['initial_credit'];
  x['DTE']=(pd.to_datetime(x.expiration)-pd.to_datetime(x.date)).dt.days; x['width']=x.short_strike-x.long_strike
  if 'short_delta' not in x: x['short_delta']=pd.NA
  populations[(a,s)]=x; ed=eligible(s,lo,hi); uq=x.entry_date.nunique(); m=metric(x,s); m.update(ATR=a,ticker=s,eligible_setup_dates=ed,qualified_trade_rows=len(x),unique_qualified_entry_dates=uq,tradable_date_coverage=uq/ed,avg_trades_per_qualified_date=len(x)/uq if uq else 0); rows.append(m)
pd.DataFrame(rows).to_csv('research_outputs/safe_strike_stage1_pass_only/full_population_metrics.csv',index=False)
matched=[]
for s in W:
 dates=set.intersection(*[{pd.Timestamp(r.date) for _,r in populations[(a,s)].iterrows()} for a in ats]);
 for a in ats: matched.append({**metric(populations[(a,s)][populations[(a,s)].entry_date.isin(dates)],s),'ATR':a,'ticker':s,'matched_N':len(dates)})
pd.DataFrame(matched).to_csv('research_outputs/safe_strike_stage1_pass_only/matched_date_metrics.csv',index=False)
print(pd.DataFrame(rows)[['ticker','ATR','eligible_setup_dates','qualified_trade_rows','unique_qualified_entry_dates','tradable_date_coverage','credit_median','touch5','touch10','stop_rate','expectancy','profit_factor','max_drawdown']].to_string(index=False)); print('matched',pd.DataFrame(matched)[['ticker','matched_N','ATR','N']].to_string(index=False))
