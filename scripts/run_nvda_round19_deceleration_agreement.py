from pathlib import Path
import json, numpy as np, pandas as pd
ROOT=Path(__file__).resolve().parents[1]; SRC=ROOT/'research_outputs/nvda_research_agent/round16_deep_entry_sequence_20260824'; OUT=ROOT/'research_outputs/nvda_research_agent/round19_deceleration_agreement_20260824'; OUT.mkdir(parents=True,exist_ok=True)
d=pd.read_parquet(SRC/'train_sequence_rows.parquet').sort_values('date'); d['candidate']=d.sequence_state.eq('DECELERATING_DOWNSIDE')&d.qqq_above50.fillna(False); d['bad']=d.sequence_state.eq('DECELERATING_DOWNSIDE')&~d.qqq_above50.fillna(False)
def m(g):
 w=g.pnl>0; return pd.Series({'n':len(g),'pnl':g.pnl.sum(),'expectancy':g.pnl.mean(),'pf':g.loc[w,'pnl'].sum()/abs(g.loc[~w,'pnl'].sum()) if (~w).any() else np.inf,'win_rate':w.mean(),'stop_rate':g.stop.mean()})
rows=[]
for eid,g in d.groupby('episode_id'):
 c=g[g.candidate]
 if len(c): rows.append({'episode_id':eid,'year':g.year.iloc[0],'n':len(c),'pnl':c.pnl.sum(),'class':'POSITIVE' if c.pnl.sum()>0 else 'NEGATIVE' if c.pnl.sum()<0 else 'FLAT'})
ep=pd.DataFrame(rows); ep.to_csv(OUT/'candidate_episode_detail.csv',index=False); first=d[d.candidate].groupby('episode_id').head(1)
pd.DataFrame({'all_candidate':m(d[d.candidate]),'first_entry':m(first),'one_per_episode':m(first),'market_weak_control':m(d[d.bad])}).T.to_csv(OUT/'headline_metrics.csv'); pd.DataFrame([{'year':y,**m(g[g.candidate]).to_dict(),'episodes':g[g.candidate].episode_id.nunique(),'positive_episodes':int((ep[ep.year.eq(y)].pnl>0).sum()),'negative_episodes':int((ep[ep.year.eq(y)].pnl<0).sum())} for y,g in d.groupby('year')]).to_csv(OUT/'annual.csv',index=False)
json.dump({'research_id':'nvda_round19_deceleration_agreement','definition':'sequence_state=DECELERATING_DOWNSIDE AND qqq_above50=True','mode':'EXISTING_TRADE','final_oos_read':False,'validation_read':False,'production_changes':False,'status':'DESCRIPTIVE_ONLY'},open(OUT/'study_manifest.json','w'),indent=2); print('episodes',len(ep),ep.to_string(index=False)); print(pd.read_csv(OUT/'headline_metrics.csv').to_string(index=False))
