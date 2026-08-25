"""Report baseline same-ticker exposure cycles from sealed artifacts."""
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

BASE=Path('research_outputs/spy_qqq_pcs_baseline_20260821'); OUT=Path('research_outputs/exposure_cycles_20260821'); OUT.mkdir(parents=True,exist_ok=True)

def one(ticker, split, sessions):
 c=pd.read_parquet(BASE/f'{ticker}_entry_contract_v2.parquet'); o=pd.read_parquet(BASE/f'{ticker}_train_validation_outcomes.parquet'); l=pd.read_parquet(BASE/f'{ticker}_lifecycle_marks.parquet')
 c['entry']=pd.to_datetime(c.decision_date); o['entry']=pd.to_datetime(o.decision_date); l['mark_date']=pd.to_datetime(l.mark_date)
 e=l[l.exit.fillna(False)].sort_values('mark_date').drop_duplicates('candidate_id')[['candidate_id','mark_date']].rename(columns={'mark_date':'exit'})
 x=c.merge(o[['candidate_id','pnl','exit_reason','stop','entry']],on=['candidate_id','entry'],how='left').merge(e,on='candidate_id',how='left').sort_values(['entry','candidate_id'])
 if split=='TRAIN': x=x[x.entry<=pd.Timestamp('2025-12-31')]
 elif split=='VALIDATION': x=x[x.entry.between('2026-01-01','2026-05-31')]
 x=x.reset_index(drop=True); cycles=[]; current=[]; end=None
 for _,r in x.iterrows():
  if current and r.entry>end: cycles.append(current); current=[]
  current.append(r); end=max([z.exit for z in current if pd.notna(z.exit)],default=r.entry)
 if current: cycles.append(current)
 rows=[]
 for i,group in enumerate(cycles,1):
  z=pd.DataFrame(group); start=z.entry.min(); finish=z.exit.max() if z.exit.notna().any() else pd.NaT
  if pd.notna(finish): days=int(((sessions >= start) & (sessions <= finish)).sum())
  else: days=None
  rows.append({'ticker':ticker,'split':split,'cycle_id':f'{ticker}_{split}_{i:03d}','cycle_start':start,'cycle_end':finish,'holding_trading_days':days,'exit_reason':'|'.join(sorted(set(z.exit_reason.dropna().astype(str)))),'pnl':z.pnl.sum(),'next_cycle_start':pd.NaT,'gap_to_next_cycle':None})
 out=pd.DataFrame(rows)
 if len(out)>1:
  out['next_cycle_start']=out.cycle_start.shift(-1); out['gap_to_next_cycle']=(out.next_cycle_start-out.cycle_end).dt.days- len([])
  # Gap is trading-session count between cycle end and next cycle start.
  out.loc[out.index[:-1],'gap_to_next_cycle']=[max(0,int(((sessions > a) & (sessions < b)).sum())) for a,b in zip(out.cycle_end.iloc[:-1],out.next_cycle_start.iloc[:-1])]
 return out

def main():
 allrows=[]; summaries=[]
 for t in ('SPY','QQQ'):
  sessions=pd.DatetimeIndex(pd.to_datetime(PCSDataAccess().read_prices(t)["date"]).dt.normalize().drop_duplicates().sort_values())
  for s in ('TRAIN','VALIDATION'):
   d=one(t,s,sessions); d.to_csv(OUT/f'{t.lower()}_{s.lower()}_cycles.csv',index=False); allrows.append(d)
   lengths=d.holding_trading_days.dropna(); gaps=d.gap_to_next_cycle.dropna(); window_days=(pd.Timestamp('2025-12-31')-pd.Timestamp('2020-02-28')).days if s=='TRAIN' else (pd.Timestamp('2026-05-31')-pd.Timestamp('2026-01-01')).days; years=window_days/365.25
   months=max(years*12,1); quarters=max(years*4,1)
   summaries.append({'ticker':t,'split':s,'total_exposure_cycles':len(d),'cycles_per_year':len(d)/years,'cycles_per_quarter':len(d)/quarters,'cycles_per_month':len(d)/months,'median_cycle_length_trading_days':lengths.median() if len(lengths) else None,'mean_cycle_length_trading_days':lengths.mean() if len(lengths) else None,'p25_cycle_length':lengths.quantile(.25) if len(lengths) else None,'p75_cycle_length':lengths.quantile(.75) if len(lengths) else None,'median_gap_between_cycles':gaps.median() if len(gaps) else None,'mean_gap_between_cycles':gaps.mean() if len(gaps) else None})
 pd.concat(allrows,ignore_index=True).to_csv(OUT/'all_exposure_cycles.csv',index=False); pd.DataFrame(summaries).to_csv(OUT/'exposure_cycle_summary.csv',index=False); print(pd.DataFrame(summaries).to_string(index=False))
if __name__=='__main__': main()
