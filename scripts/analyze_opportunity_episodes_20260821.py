from pathlib import Path
import pandas as pd

BASE=Path('research_outputs/spy_qqq_pcs_baseline_20260821'); FIX=Path('research_outputs/conditional_exposure_research_v2_20260821'); OUT=Path('research_outputs/opportunity_episode_analysis_20260821'); OUT.mkdir(parents=True,exist_ok=True)

def source(t,split,variant='BASELINE'):
 if variant=='BASELINE':
  c=pd.read_parquet(BASE/f'{t}_entry_contract_v2.parquet'); o=pd.read_parquet(BASE/f'{t}_train_validation_outcomes.parquet'); l=pd.read_parquet(BASE/f'{t}_lifecycle_marks.parquet'); c['entry']=pd.to_datetime(c.decision_date); o['entry']=pd.to_datetime(o.decision_date); l['mark_date']=pd.to_datetime(l.mark_date); e=l[l.exit.fillna(False)].sort_values('mark_date').drop_duplicates('candidate_id')[['candidate_id','mark_date']].rename(columns={'mark_date':'exit'}); x=c.merge(o[['candidate_id','pnl','stop']],on='candidate_id').merge(e,on='candidate_id',how='left')
 else:
  x=pd.read_parquet(FIX/f'{t}_ISOLATED_SPLIT_{variant}.parquet').rename(columns={'decision_date':'entry','exit_date':'exit'}); x=x[x.admitted]
 x['entry']=pd.to_datetime(x.entry); x['exit']=pd.to_datetime(x.exit); return x[x.entry.between('2020-02-28','2025-12-31') if split=='TRAIN' else x.entry.between('2026-01-01','2026-05-31')].sort_values(['entry','candidate_id']).reset_index(drop=True)

def episodes(x,gap):
 if x.empty:return pd.DataFrame()
 pos={d:i for i,d in enumerate(pd.bdate_range(x.entry.min(),x.entry.max()))}; groups=[]; current=[]; start_i=None
 for _,r in x.iterrows():
  i=pos.get(r.entry,0)
  if current and i-start_i>=gap: groups.append(current); current=[]
  if not current:start_i=i
  current.append(r)
 if current:groups.append(current)
 rows=[]
 for n,g in enumerate(groups,1):
  z=pd.DataFrame(g); end=z.exit.max() if z.exit.notna().any() else z.entry.max(); days=pd.bdate_range(z.entry.min(),end)
  rows.append({'ticker':z.iloc[0].get('ticker',None),'episode_id':n,'episode_start':z.entry.min(),'episode_end':end,'trading_days_span':len(days),'number_of_entries':len(z),'peak_simultaneous_positions':max(int(((z.entry<=d)&(z.exit.isna()|(z.exit>=d))).sum()) for d in days),'total_pnl':z.pnl.sum(),'stopped_trades':int(z.stop.sum()),'winning_trades':int((z.pnl>0).sum())})
 return pd.DataFrame(rows)

def exposure_cycles(x):
 rows=[]; cur=[]; end=None
 for _,r in x.iterrows():
  if cur and r.entry>end:rows.append(cur);cur=[]
  cur.append(r);end=max([q.exit for q in cur if pd.notna(q.exit)],default=r.entry)
 if cur:rows.append(cur)
 return len(rows)

def main():
 summaries=[]; retained=[]
 for t in ('SPY','QQQ'):
  for split in ('TRAIN','VALIDATION'):
   base=source(t,split); cycles=exposure_cycles(base)
   for gap in (10,15,20):
    ep=episodes(base,gap); ep['ticker']=t; ep.to_csv(OUT/f'{t.lower()}_{split.lower()}_episodes_{gap}d.csv',index=False); summaries.append({'ticker':t,'split':split,'baseline_trades':len(base),'exposure_cycles':cycles,'opportunity_episodes':len(ep),'trades_per_episode':len(base)/len(ep) if len(ep) else None,'episode_distribution':ep.number_of_entries.value_counts().to_dict()})
    for v in ('R1_MAX_OPEN_1','R2_MAX_OPEN_2'):
     y=source(t,split,v); ve=episodes(y,gap); retained.append({'ticker':t,'split':split,'variant':v,'gap_days':gap,'baseline_episodes':len(ep),'variant_episodes':len(ve),'pct_opportunity_episodes_retained':len(ve)/len(ep)*100 if len(ep) else None})
 pd.DataFrame(summaries).to_csv(OUT/'opportunity_episode_summary.csv',index=False); pd.DataFrame(retained).to_csv(OUT/'opportunity_episode_retention.csv',index=False); print(pd.DataFrame(summaries).to_string(index=False)); print(pd.DataFrame(retained).to_string(index=False))
if __name__=='__main__':main()
