from pathlib import Path
import json, numpy as np, pandas as pd
R=Path(__file__).resolve().parents[1]; T=R/'research_outputs/nvda_research_agent/round20_episode_timeline_20260824'; A=R/'research_outputs/nvda_research_agent/round21_authoritative_delayed_replay_20260824'; O=A
states=pd.read_csv(T/'episode_first_state_dates.csv',parse_dates=['episode_start','episode_end','first_baseline_entry','first_downside_slowdown','first_stabilizing','first_ma20_reclaim','first_qqq_above50','first_qqq_slowdown'])
c=pd.read_parquet(A/'authoritative_candidates.parquet'); l=pd.read_parquet(A/'authoritative_lifecycle.parquet'); c.date=pd.to_datetime(c.date).dt.normalize(); l.date=pd.to_datetime(l.date).dt.normalize(); l=l[l.status.eq('COMPLETE')].copy()
methods={'BASELINE_FIRST_ENTRY':'first_baseline_entry','FIRST_DOWNSIDE_SLOWDOWN':'first_downside_slowdown','FIRST_STABILIZATION':'first_stabilizing','FIRST_MA20_RECLAIM':'first_ma20_reclaim','FIRST_QQQ_CONFIRMATION':'first_qqq_above50','FIRST_QQQ_SLOWDOWN':'first_qqq_slowdown'}
priority={5:0,10:1,2:2}; c['width_rank']=c.spread_width.map(priority).fillna(9); c=c.sort_values(['date','expiration','width_rank','short_strike']); chosen=c.groupby('date',as_index=False).head(1).copy()
rows=[]
for _,e in states.iterrows():
 for method,col in methods.items():
  sig=e[col]; rec={'episode_id':e.episode_id,'baseline_entry_date':e.first_baseline_entry,'method':method,'signal_date':sig,'delay_trading_days':np.nan,'contract_available':False,'lifecycle_result':'NO_SIGNAL' if pd.isna(sig) else 'NO_CONTRACT','stopped':np.nan,'PnL':np.nan,'max_adverse_excursion':np.nan,'max_favorable_excursion':np.nan}
  if pd.notna(sig):
   sig=pd.Timestamp(sig).normalize(); rec['delay_trading_days']=len(pd.bdate_range(pd.Timestamp(e.first_baseline_entry),sig))-1; q=chosen[chosen.date.eq(sig)]
   if len(q):
    x=q.iloc[0]; z=l[l.candidate_id==x.candidate_id]; rec.update({'contract_available':True,'short_strike':x.short_strike,'long_strike':x.long_strike,'expiration':x.expiration,'DTE':x.dte,'credit':x.credit,'safe_strike_distance_ATR':(x.close-x.comparison_short_strike)/x.atr,'lifecycle_result':z.status.iloc[0] if len(z) else 'NO_LIFECYCLE','stopped':bool(z.stopped.iloc[0]) if len(z) else np.nan,'PnL':z.realized_pnl.iloc[0] if len(z) else np.nan,'max_adverse_excursion':z.mae.iloc[0] if len(z) else np.nan,'max_favorable_excursion':z.mfe.iloc[0] if len(z) else np.nan})
  rows.append(rec)
out=pd.DataFrame(rows); out.to_parquet(O/'delayed_entry_per_episode_method.parquet',index=False); out.to_csv(O/'delayed_entry_per_episode_method.csv',index=False)
def met(g):
 t=g[g.lifecycle_result.eq('COMPLETE')]; p=t.PnL.dropna(); w=p>0; return pd.Series({'EPISODES':len(g),'SIGNALS_AVAILABLE':g.signal_date.notna().sum(),'VALID_CONTRACTS':g.contract_available.sum(),'TRADES':len(t),'POSITIVE_TRADES':w.sum(),'NEGATIVE_TRADES':(~w).sum(),'STOP_RATE':t.stopped.mean() if len(t) else np.nan,'TOTAL_PNL':p.sum(),'EXPECTANCY':p.mean(),'PF':p[w].sum()/abs(p[~w].sum()) if (~w).any() else np.inf,'AVG_WIN':p[w].mean() if w.any() else np.nan,'AVG_LOSS':p[~w].mean() if (~w).any() else np.nan,'WORST_TRADE':p.min() if len(p) else np.nan,'EPISODE_COVERAGE_RATE':g.signal_date.notna().mean(),'CONTRACT_COVERAGE_RATE':g.contract_available.sum()/max(g.signal_date.notna().sum(),1)})
summary=out.groupby('method').apply(met,include_groups=False); summary.to_csv(O/'delayed_entry_summary.csv')
base=out[out.method.eq('BASELINE_FIRST_ENTRY')][['episode_id','PnL']].rename(columns={'PnL':'base_pnl'}); comp=out.merge(base,on='episode_id'); comp['better_than_baseline']=comp.PnL>comp.base_pnl; comp['worse_than_baseline']=comp.PnL<comp.base_pnl; comp.to_csv(O/'episode_comparison_to_baseline.csv')
loo=[]
for method,g in out.groupby('method'):
 for eid in g.episode_id.unique(): loo.append({'method':method,'removed_episode':eid,**met(g[g.episode_id!=eid]).to_dict()})
pd.DataFrame(loo).to_csv(O/'leave_one_episode_out.csv',index=False)
json.dump({'research_id':'nvda_authoritative_delayed_replay_round21','authoritative_components':'run_current_strategy_replay + PCSDataAccess + canonical lifecycle adapter','contract_reselection':True,'baseline_contract_reused':False,'validation_read':False,'final_oos_read':False,'production_changes':False},open(O/'delayed_replay_manifest.json','w'),indent=2); print(summary.to_string())
