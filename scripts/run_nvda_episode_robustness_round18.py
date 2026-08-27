from pathlib import Path
import json, numpy as np, pandas as pd
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'research_outputs/nvda_research_agent/round16_deep_entry_sequence_20260824'
OUT=ROOT/'research_outputs/nvda_research_agent/round18_episode_robustness_20260824'; OUT.mkdir(parents=True,exist_ok=True)
d=pd.read_parquet(SRC/'train_sequence_rows.parquet').sort_values('date').reset_index(drop=True)
d['candidate']=d.sequence_state.isin(['UNKNOWN','STABILIZING']) & d.qqq_above50.fillna(False)
d['bad']=d.sequence_state.eq('ACCELERATING_DOWNSIDE') & ~d.qqq_above50.fillna(False)
d['entry_order_within_episode']=d.groupby('episode_id').cumcount()+1
d['episode_start']=d.groupby('episode_id').date.transform('min'); d['episode_end']=d.groupby('episode_id').date.transform('max')
def metrics(g):
    if len(g)==0:return pd.Series({'n':0,'pnl':0,'expectancy':np.nan,'pf':np.nan,'win_rate':np.nan,'stop_rate':np.nan})
    w=g.pnl>0; return pd.Series({'n':len(g),'pnl':g.pnl.sum(),'expectancy':g.pnl.mean(),'pf':g.loc[w,'pnl'].sum()/abs(g.loc[~w,'pnl'].sum()) if (~w).any() else np.inf,'win_rate':w.mean(),'stop_rate':g.stop.mean()})
candidate=d[d.candidate].copy(); bad=d[d.bad].copy()
ep=[]
for eid,g in d.groupby('episode_id'):
    c=g[g.candidate]; ep.append({'episode_id':eid,'episode_start':g.date.min(),'episode_end':g.date.max(),'all_trade_count':len(g),'all_pnl':g.pnl.sum(),'candidate_trade_count':len(c),'candidate_pnl':c.pnl.sum() if len(c) else 0,'candidate_expectancy':c.pnl.mean() if len(c) else np.nan,'candidate_stop_rate':c.stop.mean() if len(c) else np.nan,'candidate_class':('POSITIVE' if c.pnl.sum()>0 else 'NEGATIVE' if c.pnl.sum()<0 else 'FLAT') if len(c) else 'NO_CANDIDATE','year':g.year.iloc[0]})
ep=pd.DataFrame(ep); cep=ep[ep.candidate_trade_count>0].copy(); ep.to_csv(OUT/'all_episode_map.csv',index=False); cep.to_csv(OUT/'candidate_episode_detail.csv',index=False)
def selected(flag): return metrics(d[d[flag]].sort_values('date').groupby('episode_id').head(1))
pd.DataFrame({'all_baseline':metrics(d),'candidate':metrics(candidate),'candidate_first_entry':selected('candidate'),'candidate_one_per_episode':selected('candidate'),'bad':metrics(bad),'bad_first_entry':selected('bad'),'bad_one_per_episode':selected('bad')}).T.to_csv(OUT/'headline_metrics.csv')
loo=[]
for eid in cep.episode_id:
    z=metrics(candidate[candidate.episode_id!=eid]); z['removed_episode_id']=eid; loo.append(z)
pd.DataFrame(loo).to_csv(OUT/'candidate_leave_one_episode_out.csv',index=False)
annual=[]
for y,g in d.groupby('year'):
    ce=g[g.candidate]; be=g[g.bad]; q=cep[cep.year.eq(y)]
    annual.append({'year':y,'total_episodes':g.episode_id.nunique(),'candidate_episodes':ce.episode_id.nunique(),'candidate_positive_episodes':int(q.candidate_class.eq('POSITIVE').sum()),'candidate_negative_episodes':int(q.candidate_class.eq('NEGATIVE').sum()),**{'candidate_'+k:v for k,v in metrics(ce).items()},'bad_episodes':be.episode_id.nunique(),**{'bad_'+k:v for k,v in metrics(be).items()}})
pd.DataFrame(annual).to_csv(OUT/'year_episode_stability.csv',index=False)
rows=[]
for eid in cep.episode_id:
    g=d[d.episode_id.eq(eid)]; c=g[g.candidate]; b=metrics(g); q=metrics(c); rows.append({'episode_id':eid,'baseline_expectancy':b.expectancy,'candidate_expectancy':q.expectancy,'baseline_stop_rate':b.stop_rate,'candidate_stop_rate':q.stop_rate,'baseline_tail_rate':(g.pnl<=g.pnl.quantile(.1)).mean(),'candidate_tail_rate':(c.pnl<=c.pnl.quantile(.1)).mean()})
pd.DataFrame(rows).to_csv(OUT/'same_episode_baseline_comparison.csv',index=False)
bad.groupby('episode_id').apply(metrics,include_groups=False).reset_index().to_csv(OUT/'bad_episode_detail.csv',index=False)
conc=cep.sort_values('candidate_pnl',ascending=False); total=candidate.pnl.sum(); concentration={'candidate_total_pnl':total,'largest_episode_pnl':conc.candidate_pnl.iloc[0],'top2_pnl':conc.head(2).candidate_pnl.sum(),'top3_pnl':conc.head(3).candidate_pnl.sum(),'top1_share':conc.head(1).candidate_pnl.sum()/total,'top2_share':conc.head(2).candidate_pnl.sum()/total,'top3_share':conc.head(3).candidate_pnl.sum()/total,'positive_candidate_episodes':int(cep.candidate_class.eq('POSITIVE').sum()),'negative_candidate_episodes':int(cep.candidate_class.eq('NEGATIVE').sum()),'positive_episode_ratio':float(cep.candidate_class.eq('POSITIVE').mean())}; json.dump(concentration,open(OUT/'concentration.json','w'),indent=2)
decision='PROMISING_BUT_INSUFFICIENT' if len(cep)>=3 and cep.candidate_class.eq('POSITIVE').mean()>=.5 else 'REJECT'
json.dump({'research_id':'nvda_episode_robustness_round18','round17_exact_definition':"sequence_state in {UNKNOWN, STABILIZING} AND qqq_above50=True; episode gap > 10 calendar days",'candidate_definition_unchanged':True,'validation_read':False,'final_oos_read':False,'production_changes':False,'freeze_decision':decision},open(OUT/'study_manifest.json','w'),indent=2)
print('CANDIDATE EPISODES',len(cep),'POS',int(cep.candidate_class.eq('POSITIVE').sum()),'NEG',int(cep.candidate_class.eq('NEGATIVE').sum())); print(pd.read_csv(OUT/'headline_metrics.csv').to_string(index=False)); print(pd.read_csv(OUT/'candidate_leave_one_episode_out.csv').describe().to_string())
