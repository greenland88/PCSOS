from pathlib import Path
import json, sys
import pandas as pd
sys.path.insert(0,'src')
from pcs.research.same_ticker_state import SameTickerState

BASE=Path('research_outputs/spy_qqq_pcs_baseline_20260821'); OUT=Path('research_outputs/same_ticker_stacking_research_fixed_20260821'); OUT.mkdir(parents=True,exist_ok=True)

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
 x['support_state']=[sm.get(d.date()) for d in x.decision_date]; return x

def admit(x, split=None):
 y=x if split is None else x[x.split.eq(split)].copy(); state=SameTickerState(); out=[]
 for _,r in y.sort_values(['decision_date','candidate_id']).iterrows():
  state.release(r.decision_date.date()); before=state.count(); weak=pd.isna(r.support_state) or str(r.support_state).upper() in {'WEAK','NO_VALID_SUPPORT','UNKNOWN','NONE','NAN'}
  decision=state.decide(str(r.candidate_id),r.exit_date.date() if pd.notna(r.exit_date) else None)
  out.append({**r.to_dict(),'open_before':before,'r1_admit':not weak,'r2_decision':decision,'r2_admit':decision=='OPEN','r3_admit':(not weak and decision=='OPEN')})
 return pd.DataFrame(out)

def metrics(x, flag):
 a=x[x[flag]].copy(); rej=x[~x[flag]].copy(); p=a.pnl.astype(float); w=p[p>0]; l=p[p<0]; curve=p.cumsum(); dd=curve-curve.cummax()
 return {'trades':len(a),'rejected_trades':len(rej),'expectancy':p.mean() if len(p) else None,'profit_factor':w.sum()/abs(l.sum()) if len(l) else None,'total_pnl':p.sum(),'stop_rate':a.stop.mean() if len(a) else None,'max_drawdown':dd.min() if len(p) else None,'worst_trade':p.min() if len(p) else None,'tail_losses':int((p<=-200).sum()),'rejected_winners':int((rej.pnl>0).sum()),'rejected_losers':int((rej.pnl<0).sum()),'pnl_of_rejected_trades':rej.pnl.sum(),'max_simultaneous_positions':int(a.open_before.max()+1) if len(a) else 0}

def main():
 report={'module':'same_ticker_stacking_research_fixed','version':'20260821.v1','primary_split_contract':'ISOLATED_SPLIT','secondary':'CONTINUOUS_DEPLOYMENT_SENSITIVITY','old_results':'INVALID_SUPERSEDED','production_rules_changed':False,'cross_ticker_bucket_tested':False,'tickers':{}}
 allrows=[]
 for t in ('SPY','QQQ'):
  x=load(t); info={}
  for mode in ('ISOLATED_SPLIT','CONTINUOUS_DEPLOYMENT_SENSITIVITY'):
   z=admit(x) if mode=='CONTINUOUS_DEPLOYMENT_SENSITIVITY' else pd.concat([admit(x,'TRAIN'),admit(x,'VALIDATION'),admit(x,'FINAL_OOS')])
   z.to_parquet(OUT/f'{t}_{mode}_rows.parquet',index=False)
   for split in ('TRAIN','VALIDATION','FINAL_OOS'):
    q=z[z.split.eq(split)]; q=q.copy(); q['r0']=True
    for v,f in [('R0_BASELINE','r0'),('R1_NO_WEAK_SUPPORT','r1_admit'),('R2_NO_SAME_TICKER_STACKING','r2_admit'),('R3_NO_WEAK_SUPPORT_PLUS_NO_SAME_TICKER_STACKING','r3_admit')]:
     m=metrics(q,f); m.update({'ticker':t,'mode':mode,'variant':v,'split':split}); allrows.append(m)
  z=admit(x); june=z[z.decision_date.between('2026-06-01','2026-06-04')]; info['june_full_chrono']={'baseline_pnl':float(june.pnl.sum()),'r2_pnl':float(june.loc[june.r2_admit,'pnl'].sum()),'r2_admitted':int(june.r2_admit.sum()),'decisions':june[['decision_date','candidate_id','open_before','r2_decision','pnl']].to_dict('records')}; report['tickers'][t]=info
 report['metrics']=allrows; pd.DataFrame(allrows).to_csv(OUT/'variant_metrics.csv',index=False); (OUT/'research_report.json').write_text(json.dumps(report,indent=2,default=str)); print(json.dumps(report,indent=2,default=str))

if __name__=='__main__': main()
