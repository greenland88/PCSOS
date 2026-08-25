from pathlib import Path
import json, numpy as np, pandas as pd
from pcs.data.access import PCSDataAccess
R=Path(__file__).resolve().parents[1]; S=R/'research_outputs/nvda_research_agent/round16_deep_entry_sequence_20260824'; O=R/'research_outputs/nvda_research_agent/round20_episode_timeline_20260824'; O.mkdir(parents=True,exist_ok=True)
d=pd.read_parquet(S/'train_sequence_rows.parquet').sort_values('date'); p=PCSDataAccess().read_prices('NVDA','2019-01-01','2023-12-31'); p.date=pd.to_datetime(p.date).dt.normalize(); p=p.sort_values('date').drop_duplicates('date').set_index('date'); q=PCSDataAccess().read_prices('QQQ','2019-01-01','2023-12-31'); q.date=pd.to_datetime(q.date).dt.normalize(); q=q.sort_values('date').drop_duplicates('date').set_index('date')
p['ret1']=p.close.pct_change(); p['ret3']=p.close.pct_change(3); p['ma20']=p.close.rolling(20).mean(); p['ma50']=p.close.rolling(50).mean(); p['ma20_dist']=p.close/p.ma20-1; p['ma50_dist']=p.close/p.ma50-1; p['ma20_slope']=p.ma20.pct_change(5); p['ma50_slope']=p.ma50.pct_change(10); p['ma20_reclaim']=(p.close>p.ma20)&(p.close.shift(1)<=p.ma20.shift(1)); p['stabilizing']=(p.ret3.abs()<.03)&(p.ma20_slope.abs()<.01); p['downside_slowdown']=p.ret3.lt(0)&p.ret3.ge(p.ret3.shift(3)); p['qqq_close']=q.close.reindex(p.index); p['qqq_ma50']=p.qqq_close.rolling(50).mean(); p['qqq_above50']=p.qqq_close>p.qqq_ma50; p['qqq_ret3']=q.close.reindex(p.index).pct_change(3); p['qqq_slowdown']=p.qqq_ret3.lt(0)&p.qqq_ret3.ge(p.qqq_ret3.shift(3)); p['rs20']=p.close.pct_change(20)-q.close.reindex(p.index).pct_change(20)
tl=[]; ev=[]
for eid,g in d.groupby('episode_id'):
 s,e=g.date.min(),g.date.max(); z=p.loc[max(p.index.min(),s-pd.offsets.BDay(5)):e].reset_index(); z['episode_id']=eid; tl.append(z); row={'episode_id':eid,'episode_start':s,'episode_end':e,'first_baseline_entry':s}
 for c in ['downside_slowdown','stabilizing','ma20_reclaim','qqq_above50','qqq_slowdown']:
  a=z[z[c].fillna(False)]; row['first_'+c]=a.date.min() if len(a) else pd.NaT
 ev.append(row)
pd.concat(tl,ignore_index=True).to_parquet(O/'episode_daily_timelines.parquet',index=False); pd.DataFrame(ev).to_csv(O/'episode_first_state_dates.csv',index=False); d.groupby('episode_id').head(1).to_csv(O/'baseline_first_entries.csv',index=False)
f=d.groupby('episode_id').head(1); rows=[]
for k in range(4): rows.append({'delay_days':k,'episodes':len(f),'diagnostic_original_pnl_not_reassigned':f.pnl.sum(),'diagnostic_original_expectancy_not_reassigned':f.pnl.mean()})
pd.DataFrame(rows).to_csv(O/'delay_diagnostic_not_replayed.csv',index=False); json.dump({'research_id':'nvda_episode_timeline_round20','episode_definition':'existing round16 episode_id; >10 calendar-day gap','counterfactual_contract_reselection':'NOT_RUN','counterfactual_lifecycle':'NOT_RUN','validation_read':False,'final_oos_read':False,'production_changes':False,'status':'DESCRIPTIVE_ONLY'},open(O/'study_manifest.json','w'),indent=2); print('episodes',d.episode_id.nunique(),'first entries',len(f))
