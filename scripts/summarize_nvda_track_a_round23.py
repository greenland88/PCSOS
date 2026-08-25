from pathlib import Path
import json,numpy as np,pandas as pd
R=Path(__file__).resolve().parents[1]; T=R/'research_outputs/nvda_episode_timeline_20260824'; A=R/'research_outputs/nvda_research_agent/track_a_round23_delayed_replay_20260824'; O=A
s=pd.read_csv(R/'research_outputs/nvda_research_agent/round20_episode_timeline_20260824/episode_first_state_dates.csv',parse_dates=['episode_start','episode_end','first_baseline_entry','first_downside_slowdown','first_stabilizing','first_ma20_reclaim','first_qqq_above50','first_qqq_slowdown']); c=pd.read_parquet(A/'track_a_candidates.parquet'); l=pd.read_parquet(A/'track_a_lifecycle.parquet'); c.date=pd.to_datetime(c.date).dt.normalize(); l.date=pd.to_datetime(l.date).dt.normalize(); l=l[l.status.eq('COMPLETE')]; methods={'BASELINE_FIRST_ENTRY':'first_baseline_entry','FIRST_DOWNSIDE_SLOWDOWN':'first_downside_slowdown','FIRST_STABILIZATION':'first_stabilizing','FIRST_MA20_RECLAIM':'first_ma20_reclaim','FIRST_QQQ_CONFIRMATION':'first_qqq_above50','FIRST_QQQ_SLOWDOWN':'first_qqq_slowdown'}; priority={5:0,10:1,2:2}; c['wr']=c.spread_width.map(priority).fillna(9); chosen=c.sort_values(['date','expiration','wr','short_strike']).groupby('date',as_index=False).head(1)
near=pd.read_csv(R/'research_outputs/nvda_research_agent/round22_episode_contract_coverage_20260824/nearby_date_diagnostics.csv'); near.date=pd.to_datetime(near.date).dt.normalize()
rows=[]
for _,e in s.iterrows():
 for m,col in methods.items():
  sig=e[col]; r={'episode_id':e.episode_id,'method':m,'baseline_entry_date':e.first_baseline_entry,'signal_date':sig,'contract_available':False,'execution_result':'NO_SIGNAL' if pd.isna(sig) else 'EXECUTION_GATE_FAIL'}
  if pd.notna(sig):
   sig=pd.Timestamp(sig).normalize(); q=chosen[chosen.date.eq(sig)]
   if len(q):
    x=q.iloc[0]; z=l[l.candidate_id.eq(x.candidate_id)]; r.update({'contract_available':True,'execution_result':'VALID_CONTRACT','short_strike':x.short_strike,'long_strike':x.long_strike,'expiration':x.expiration,'DTE':x.dte,'credit':x.credit,'safe_strike_distance_ATR':(x.close-x.comparison_short_strike)/x.atr,'stopped':z.stopped.iloc[0] if len(z) else np.nan,'PnL':z.realized_pnl.iloc[0] if len(z) else np.nan,'mae':z.mae.iloc[0] if len(z) else np.nan,'mfe':z.mfe.iloc[0] if len(z) else np.nan})
   else:
    a=near[near.date.eq(sig)].sort_values('offset_trading_days');
    if len(a):
     stage=a.failure_stage.iloc[0]; reason=a.failure_reason.iloc[0]
     if stage=='NONE': stage='EVENT'; reason='EVENT_GATE_REJECTED_OR_EXPIRATION_CROSSING'
     r.update({'execution_failure_stage':stage,'execution_failure_reason':reason})
  rows.append(r)
o=pd.DataFrame(rows); o.to_csv(O/'track_a_per_episode_method.csv',index=False); o.to_parquet(O/'track_a_per_episode_method.parquet',index=False)
def met(g):
 t=g[g.execution_result.eq('VALID_CONTRACT')]; p=t.PnL.dropna(); w=p>0; return pd.Series({'EPISODES_TOTAL':len(g),'SIGNALS_AVAILABLE':g.signal_date.notna().sum(),'EXECUTABLE_EPISODES':len(t),'EXECUTION_COVERAGE_RATE':len(t)/len(g),'TRADES':len(t),'TOTAL_PNL':p.sum(),'EXPECTANCY':p.mean(),'PF':p[w].sum()/abs(p[~w].sum()) if (~w).any() else np.inf,'STOP_RATE':t.stopped.mean() if len(t) else np.nan,'AVG_WIN':p[w].mean() if w.any() else np.nan,'AVG_LOSS':p[~w].mean() if (~w).any() else np.nan,'WORST_TRADE':p.min() if len(p) else np.nan})
summary=o.groupby('method').apply(met,include_groups=False); summary.to_csv(O/'track_a_summary.csv')
base=o[o.method.eq('BASELINE_FIRST_ENTRY')][['episode_id','PnL']].rename(columns={'PnL':'baseline_pnl'}); z=o.merge(base,on='episode_id'); z.to_csv(O/'track_a_episode_comparison.csv',index=False)
loo=[]
for m,g in o.groupby('method'):
 for eid in g.episode_id.unique(): loo.append({'method':m,'removed_episode':eid,**met(g[g.episode_id.ne(eid)]).to_dict()})
pd.DataFrame(loo).to_csv(O/'track_a_leave_one_episode_out.csv',index=False); json.dump({'research_id':'nvda_track_a_round23','semantics':'frozen episode qualification; execution-only gates at delayed date','setup_gates_reapplied':False,'validation_read':False,'final_oos_read':False,'production_changes':False},open(O/'track_a_manifest.json','w'),indent=2); print(summary.to_string()); print(o.groupby(['method','execution_result']).size().to_string())
