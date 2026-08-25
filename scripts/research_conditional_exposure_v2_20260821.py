"""Research-only conditional same-ticker exposure variants R0-R6."""
from pathlib import Path
import json
import pandas as pd
from pcs.research.same_ticker_state import SameTickerState

BASE=Path('research_outputs/spy_qqq_pcs_baseline_20260821'); OUT=Path('research_outputs/conditional_exposure_research_v2_20260821'); OUT.mkdir(parents=True,exist_ok=True)
VARIANTS=['R0_BASELINE','R1_MAX_OPEN_1','R2_MAX_OPEN_2','R3_SECOND_ENTRY_REQUIRES_SUPPORT','R4_SECOND_ENTRY_REQUIRES_EXISTING_POSITION_DE_RISK','R5_MAX2_SUPPORT_AND_DERISK','R6_NO_CONSECUTIVE_ENTRY_WHILE_OPEN']

def load(t):
 c=pd.read_parquet(BASE/f'{t}_entry_contract_v2.parquet'); o=pd.read_parquet(BASE/f'{t}_train_validation_outcomes.parquet'); l=pd.read_parquet(BASE/f'{t}_lifecycle_marks.parquet')
 c['decision_date']=pd.to_datetime(c.decision_date); o['decision_date']=pd.to_datetime(o.decision_date); l['mark_date']=pd.to_datetime(l.mark_date)
 e=l[l.exit.fillna(False)].sort_values('mark_date').drop_duplicates('candidate_id')[['candidate_id','mark_date']].rename(columns={'mark_date':'exit_date'})
 x=c.merge(o,on='candidate_id',suffixes=('','_outcome')).merge(e,on='candidate_id',how='left').sort_values(['decision_date','candidate_id']).reset_index(drop=True)
 x['split']=x.decision_date.map(lambda d:'TRAIN' if d<=pd.Timestamp('2025-12-31') else 'VALIDATION' if d<=pd.Timestamp('2026-05-31') else 'FINAL_OOS')
 p=Path(f'research_outputs/safe_strike_risk_map_v0_1/trend_histories/{t}_trend.parquet'); sm={}
 if p.exists():
  z=pd.read_parquet(p)
  for _,r in z.iterrows():
   try: sm[pd.Timestamp(r.date).date()]=json.loads(r.support).get('support_confluence_state')
   except Exception: pass
 x['support_state']=[sm.get(d.date()) for d in x.decision_date]
 # Existing 50% profit milestone: preserve its actual PIT mark date.
 der={}
 for cid,g in l.groupby('candidate_id'):
  cr=float(x.loc[x.candidate_id.eq(cid),'credit'].iloc[0]); hit=g[g.spread_mark <= cr*0.5]
  der[cid]=hit.mark_date.min() if len(hit) else pd.NaT
 x['derisk_date']=[der.get(cid,pd.NaT) for cid in x.candidate_id]
 return x

def admit(x, variant, split=None):
 y=x if split is None else x[x.split.eq(split)].copy(); active=[]; out=[]; prior_admit_date=None
 for _,r in y.sort_values(['decision_date','candidate_id']).iterrows():
  day=r.decision_date; active=[p for p in active if p['exit_date'] is None or pd.Timestamp(p['exit_date']) >= day]
  n=len(active); weak=pd.isna(r.support_state) or str(r.support_state).upper() in {'WEAK','NO_VALID_SUPPORT','UNKNOWN','NONE','NAN'}; decision='OPEN'
  if variant=='R1_MAX_OPEN_1' and n>=1: decision='REJECT_SAME_TICKER_MAX_OPEN_1'
  elif variant in {'R2_MAX_OPEN_2','R3_SECOND_ENTRY_REQUIRES_SUPPORT','R4_SECOND_ENTRY_REQUIRES_EXISTING_POSITION_DE_RISK','R5_MAX2_SUPPORT_AND_DERISK','R6_NO_CONSECUTIVE_ENTRY_WHILE_OPEN'}:
   if n>=2: decision='REJECT_SAME_TICKER_MAX_OPEN_2'
   elif n==1:
    if variant in {'R3_SECOND_ENTRY_REQUIRES_SUPPORT','R5_MAX2_SUPPORT_AND_DERISK'} and weak: decision='REJECT_ADDITIONAL_ENTRY_SUPPORT'
    if variant in {'R4_SECOND_ENTRY_REQUIRES_EXISTING_POSITION_DE_RISK','R5_MAX2_SUPPORT_AND_DERISK'} and not any(pd.notna(p.get('derisk_date')) and pd.Timestamp(p['derisk_date']) < day for p in active): decision='REJECT_ADDITIONAL_ENTRY_EXISTING_POSITION_NOT_DERISKED'
    if variant=='R6_NO_CONSECUTIVE_ENTRY_WHILE_OPEN' and prior_admit_date is not None and (day-prior_admit_date).days<=3: decision='REJECT_CONSECUTIVE_ENTRY_WHILE_OPEN'
  admitted=decision=='OPEN'
  out.append({**r.to_dict(),'variant':variant,'open_before':n,'decision':decision,'admitted':admitted,'depth':n+1 if admitted else n})
  if admitted: active.append({'candidate_id':r.candidate_id,'exit_date':r.exit_date.date() if pd.notna(r.exit_date) else None,'derisk_date':r.derisk_date}); prior_admit_date=day
 return pd.DataFrame(out)

def metrics(x):
 a=x[x.admitted]; rej=x[~x.admitted]; p=a.pnl.astype(float); w=p[p>0]; l=p[p<0]; curve=p.cumsum(); dd=curve-curve.cummax(); base=len(x)
 return {'trades':len(a),'trade_reduction_pct':(1-len(a)/base)*100 if base else 0,'trade_retention_pct':len(a)/base*100 if base else 0,'total_pnl':p.sum(),'pnl_retention_pct':p.sum()/x.pnl.sum()*100 if x.pnl.sum() else None,'expectancy':p.mean() if len(p) else None,'pf':w.sum()/abs(l.sum()) if len(l) else None,'win_rate':(p>0).mean() if len(p) else None,'stop_rate':a.stop.mean() if len(a) else None,'avg_winner':w.mean() if len(w) else None,'avg_loser':l.mean() if len(l) else None,'worst_trade':p.min() if len(p) else None,'max_drawdown':dd.min() if len(p) else None,'tail_loss_count':int((p<=-200).sum()),'rejected_winners':int((rej.pnl>0).sum()),'rejected_winner_pnl':rej.loc[rej.pnl>0,'pnl'].sum(),'rejected_losers':int((rej.pnl<0).sum()),'rejected_loser_pnl':rej.loc[rej.pnl<0,'pnl'].sum(),'max_simultaneous_open':int(a.open_before.max()+1) if len(a) else 0}

def main():
 report={'module':'conditional_same_ticker_exposure_research_v2','version':'20260821.v1','primary':'ISOLATED_SPLIT','secondary':'CONTINUOUS_DEPLOYMENT_SENSITIVITY','final_oos_rule_selection':False,'production_rules_changed':False,'cross_ticker_bucket_tested':False,'old_results':'INVALID_SUPERSEDED','tickers':{}}
 metrics_rows=[]
 for t in ('SPY','QQQ'):
  x=load(t); info={}
  for mode in ('ISOLATED_SPLIT','CONTINUOUS_DEPLOYMENT_SENSITIVITY'):
   for v in VARIANTS:
    z= pd.concat([admit(x,v,s) for s in ('TRAIN','VALIDATION','FINAL_OOS')],ignore_index=True) if mode=='ISOLATED_SPLIT' else admit(x,v)
    z.to_parquet(OUT/f'{t}_{mode}_{v}.parquet',index=False)
    for s in ('TRAIN','VALIDATION','FINAL_OOS'):
     m=metrics(z[z.split.eq(s)]); m.update({'ticker':t,'mode':mode,'variant':v,'split':s}); metrics_rows.append(m)
  z=admit(x,'R2_MAX_OPEN_2'); j=z[z.decision_date.between('2026-06-01','2026-06-04')]; info['june_r2_max2']={'decisions':j[['decision_date','candidate_id','open_before','decision','pnl']].to_dict('records'),'pnl':j.loc[j.admitted,'pnl'].sum()}; report['tickers'][t]=info
 pd.DataFrame(metrics_rows).to_csv(OUT/'variant_metrics.csv',index=False); (OUT/'research_report.json').write_text(json.dumps(report,indent=2,default=str)); print(json.dumps(report,indent=2,default=str))
if __name__=='__main__': main()
