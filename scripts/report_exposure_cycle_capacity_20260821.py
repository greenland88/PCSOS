from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

BASE=Path('research_outputs/spy_qqq_pcs_baseline_20260821'); FIX=Path('research_outputs/conditional_exposure_research_v2_20260821'); OUT=Path('research_outputs/exposure_capacity_20260821'); OUT.mkdir(parents=True,exist_ok=True)

def load(t,split,variant):
 if variant=='BASELINE':
  c=pd.read_parquet(BASE/f'{t}_entry_contract_v2.parquet'); o=pd.read_parquet(BASE/f'{t}_train_validation_outcomes.parquet'); l=pd.read_parquet(BASE/f'{t}_lifecycle_marks.parquet'); c['entry']=pd.to_datetime(c.decision_date); o['entry']=pd.to_datetime(o.decision_date); l['mark_date']=pd.to_datetime(l.mark_date); e=l[l.exit.fillna(False)].sort_values('mark_date').drop_duplicates('candidate_id')[['candidate_id','mark_date']].rename(columns={'mark_date':'exit'}); x=c.merge(o[['candidate_id','pnl']],on='candidate_id').merge(e,on='candidate_id',how='left')
 else:
  x=pd.read_parquet(FIX/f'{t}_ISOLATED_SPLIT_{variant}.parquet').rename(columns={'decision_date':'entry','exit_date':'exit','admitted':'admit'}); x=x[x.admit]
 x['entry']=pd.to_datetime(x.entry); x['exit']=pd.to_datetime(x.exit); return x[x.entry.between('2020-02-28','2025-12-31') if split=='TRAIN' else x.entry.between('2026-01-01','2026-05-31')].sort_values(['entry','candidate_id'])

def cycles(x):
 out=[]; cur=[]; end=None
 for _,r in x.iterrows():
  if cur and r.entry>end: out.append(cur); cur=[]
  cur.append(r); end=max([z.exit for z in cur if pd.notna(z.exit)],default=r.entry)
 if cur: out.append(cur)
 return out

def exposure(x,start,end,sessions):
 days=sessions[(sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))]; counts=[int(((x.entry<=d)&(x.exit.isna()|(x.exit>=d))).sum()) for d in days]; s=pd.Series(counts,index=days); runs=[]; cur=0
 for v in s>0: cur=cur+1 if v else 0; runs.append(cur)
 years=(pd.Timestamp(end)-pd.Timestamp(start)).days/365.25
 return {'exposure_days_per_year':float((s>0).sum()/years),'pct_time_with_any_open_pcs':float((s>0).mean()*100),'pct_time_with_1_open':float((s==1).mean()*100),'pct_time_with_2_open':float((s==2).mean()*100),'pct_time_with_3_plus_open':float((s>=3).mean()*100),'max_continuous_exposure_days':max(runs,default=0)}

def main():
 rows=[]
 for t in ('SPY','QQQ'):
  sessions=pd.DatetimeIndex(pd.to_datetime(PCSDataAccess().read_prices(t)["date"]).dt.normalize().drop_duplicates().sort_values())
  for split,(start,end) in {'TRAIN':('2020-02-28','2025-12-31'),'VALIDATION':('2026-01-01','2026-05-31')}.items():
   data={v:load(t,split,v) for v in ('BASELINE','R1_MAX_OPEN_1','R2_MAX_OPEN_2')}; cs={v:cycles(x) for v,x in data.items()}; row={'ticker':t,'split':split,'baseline_cycles':len(cs['BASELINE']),'max1_cycles':len(cs['R1_MAX_OPEN_1']),'max2_cycles':len(cs['R2_MAX_OPEN_2']),'baseline_trades_per_cycle':len(data['BASELINE'])/len(cs['BASELINE']) if cs['BASELINE'] else None,'max1_trades_per_cycle':len(data['R1_MAX_OPEN_1'])/len(cs['R1_MAX_OPEN_1']) if cs['R1_MAX_OPEN_1'] else None,'max2_trades_per_cycle':len(data['R2_MAX_OPEN_2'])/len(cs['R2_MAX_OPEN_2']) if cs['R2_MAX_OPEN_2'] else None,'pct_independent_cycles_retained_max1':len(cs['R1_MAX_OPEN_1'])/len(cs['BASELINE'])*100 if cs['BASELINE'] else None,'pct_independent_cycles_retained_max2':len(cs['R2_MAX_OPEN_2'])/len(cs['BASELINE'])*100 if cs['BASELINE'] else None}
   for v in data: row.update({f'{v.lower()}_{k}':val for k,val in exposure(data[v],start,end,sessions).items()})
   rows.append(row)
 out=pd.DataFrame(rows); out.to_csv(OUT/'exposure_capacity_summary.csv',index=False); print(out.to_string(index=False))
if __name__=='__main__': main()
