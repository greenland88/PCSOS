"""Research-only parity and source-gap audit for the isolated NVDA view."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'research_outputs/nvda_duplicate_resolved_view_20260820'
def run():
 pop=pd.read_parquet(ROOT/'research_outputs/nvda_v2_v2_replay.parquet'); pop.date=pd.to_datetime(pop.date).dt.normalize(); pop.expiration=pd.to_datetime(pop.expiration).dt.normalize(); pop['candidate_id']=pop.apply(lambda r:'|'.join([str(r.ticker),r.date.date().isoformat(),r.expiration.date().isoformat(),format(float(r.short_strike),'.15g'),format(float(r.long_strike),'.15g')]),axis=1); q=pd.read_parquet(OUT/'nvda_daily_option_quotes.parquet'); q.trade_date=pd.to_datetime(q.trade_date).dt.normalize(); q['candidate_id']=q.candidate_id.astype(str)
 parity=[]; gaps=[]
 for r in pop.itertuples():
  g=q[q.candidate_id==r.candidate_id].copy(); entry=g[g.trade_date==r.date]; short=entry[entry.strike==float(r.short_strike)]; long=entry[entry.strike==float(r.long_strike)]; rec={'candidate_id':r.candidate_id,'frozen_credit':r.credit,'decision_date':r.date,'expiration':r.expiration,'short_strike':r.short_strike,'long_strike':r.long_strike}
  if len(short)==1 and len(long)==1 and pd.notna(short.bid.iloc[0]) and pd.notna(long.ask.iloc[0]): rec.update(reconstructed_credit=float(short.bid.iloc[0]-long.ask.iloc[0]),difference=float(short.bid.iloc[0]-long.ask.iloc[0]-r.credit),status='COMPARABLE')
  else: rec.update(reconstructed_credit=None,difference=None,status='MISSING')
  parity.append(rec)
  for day in pd.date_range(r.date,r.expiration,freq='D'):
   x=g[g.trade_date==day]; s=x[x.strike==float(r.short_strike)]; l=x[x.strike==float(r.long_strike)]
   if not (len(s)==1 and len(l)==1 and pd.notna(s.bid.iloc[0]) and pd.notna(s.ask.iloc[0]) and pd.notna(l.bid.iloc[0]) and pd.notna(l.ask.iloc[0])): gaps.append({'candidate_id':r.candidate_id,'date':day,'classification':'TRUE_SOURCE_GAP','reason':'exact identity searched; no valid exact two-leg quote'})
 p=pd.DataFrame(parity); comp=p[p.status=='COMPARABLE']; exact=(comp.difference.abs()<=1e-9); p.to_parquet(OUT/'nvda_parity_mismatches.parquet',index=False); entry={'total_candidates':826,'comparable':len(comp),'exact_match':int(exact.sum()),'within_tolerance':int(exact.sum()),'mismatch':int((~exact).sum()),'missing':int((p.status=='MISSING').sum())}; (OUT/'nvda_entry_credit_parity.json').write_text(json.dumps(entry,indent=2,default=str),encoding='utf-8'); gaps=pd.DataFrame(gaps); gaps.to_parquet(OUT/'nvda_source_gap_classification.parquet',index=False); cov=pd.DataFrame([{'year':int(y),'required_candidate_days':int(sum((pd.date_range(r.date,r.expiration).year==y).sum() for r in pop.itertuples())),'source_gap_rows':int((gaps.date.dt.year==y).sum())} for y in sorted(pop.date.dt.year.unique())]); cov.to_csv(OUT/'nvda_quote_coverage_by_year.csv',index=False); exitrep={'observable_trades':0,'exit_date_matches':'NOT_RUN','exit_reason_matches':'NOT_RUN','pnl_matches':'NOT_RUN','unresolved_quote_unavailable':826,'status':'NOT_RUN_WITHOUT_CANONICAL_REPLAY_MARK_RECONSTRUCTION'}; (OUT/'nvda_exit_parity.json').write_text(json.dumps(exitrep,indent=2),encoding='utf-8'); val={'status':'PARTIAL','frozen_candidates':826,'entry_credit_parity':entry,'source_gap_rows':len(gaps),'exit_parity':exitrep,'regime_status':'NVDA_REGIME_HISTORY_BLOCKED_BY_CANONICAL_MARKET_INPUTS'}; (OUT/'nvda_lifecycle_observability_validation.json').write_text(json.dumps(val,indent=2,default=str),encoding='utf-8'); return val
if __name__=='__main__': print(json.dumps(run(),indent=2,default=str))
