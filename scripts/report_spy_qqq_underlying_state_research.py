"""Read-only final reporting for the already-run underlying-state study."""
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'research_outputs'/'spy_qqq_underlying_state_research_20260821'
def main():
 x=pd.read_parquet(OUT/'candidate_state_attribution.parquet');base=x[x.scenario_id.eq('UNDERLYING_STATE_DISABLED_BASELINE')].copy();m=pd.read_csv(OUT/'underlying_state_policy_comparison.csv')
 q=[]
 v=base[(base.split.eq('validation'))&(base.ticker.eq('QQQ'))]
 for state in ['UPTREND','PULLBACK_IN_UPTREND','STABILIZING','DOWNTREND','BREAKDOWN','RECOVERY_RECLAIM','UNKNOWN']:
  z=v[v.final_underlying_state.eq(state)];q.append({'question':'QQQ_VALIDATION_PNL_BY_STATE','state':state,'entries':len(z),'pnl':float(z.pnl.sum())})
 q.append({'question':'QQQ_VALIDATION_DOWNTREND_OR_BREAKDOWN','state':'DOWNTREND_OR_BREAKDOWN','entries':int(v.final_underlying_state.isin(['DOWNTREND','BREAKDOWN']).sum()),'pnl':float(v[v.final_underlying_state.isin(['DOWNTREND','BREAKDOWN'])].pnl.sum())})
 for scenario in ['BLOCK_DOWNTREND','BLOCK_DOWNTREND_AND_BREAKDOWN','PULLBACK_REQUIRES_STABILIZING','RECOVERY_REQUIRES_RECONFIRMATION']:
  for split in ['train','validation']:
   z=m[(m.scenario_id.eq(scenario))&(m.split.eq(split))&(m.policy.eq('UNCAPPED_BASELINE'))]
   for scope in ['QQQ','SPY+QQQ']:
    a=z[z.scope.eq(scope)].iloc[0]; b=m[(m.scenario_id.eq('UNDERLYING_STATE_DISABLED_BASELINE'))&(m.split.eq(split))&(m.scope.eq(scope))&(m.policy.eq('UNCAPPED_BASELINE'))].iloc[0]
    q.append({'question':scenario+'_IMPROVEMENT','split':split,'scope':scope,'pnl':a.total_pnl,'baseline_pnl':b.total_pnl,'pnl_delta':a.total_pnl-b.total_pnl,'expectancy_delta':a.expectancy-b.expectancy,'pf_delta':a.profit_factor-b.profit_factor})
 spy=base[(base.ticker.eq('SPY'))&(base.final_underlying_state.eq('UPTREND'))]
 for split,g in spy.groupby('split'):
  p=g.pnl; q.append({'question':'SPY_UPTREND','split':split,'entries':len(g),'expectancy':float(p.mean()),'profit_factor':float(p[p>0].sum()/abs(p[p<0].sum())) if (p<0).any() else None,'pnl':float(p.sum())})
 pd.DataFrame(q).to_csv(OUT/'direct_question_answers.csv',index=False)
 # Same-day loss and planned-loss concurrency using the already selected, fixed lifecycle rows.
 rows=[]
 for scenario,g in x.groupby('scenario_id'):
  for split,h in g.groupby('split'):
   for ticker,k in list(h.groupby('ticker'))+[('SPY+QQQ',h)]: rows.append({'scenario_id':scenario,'split':split,'scope':ticker,'worst_same_day_correlated_loss':float(k.groupby('exit_date').pnl.sum().min()),'peak_planned_loss_entries_sum':float(k.planned_loss.sum())})
 pd.DataFrame(rows).to_csv(OUT/'portfolio_concurrency_by_underlying_state.csv',index=False)
 # State × scale-in order is an attribution of the fixed lifecycle population;
 # it never reselects a contract or recomputes an exit.
 scale=[]
 for (scenario,split,ticker,state),g in x.groupby(['scenario_id','split','ticker','final_underlying_state'],dropna=False):
  for order,h in g.assign(scale_bucket=g.scale_in_order.map(lambda n:'4+' if n>=4 else str(int(n)))).groupby('scale_bucket'):
   pnl=h.pnl; scale.append({'scenario_id':scenario,'split':split,'ticker':ticker,'underlying_state':state if pd.notna(state) else 'UNKNOWN','scale_in_order':order,'entries':len(h),'total_pnl':float(pnl.sum()),'expectancy':float(pnl.mean()),'profit_factor':float(pnl[pnl>0].sum()/abs(pnl[pnl<0].sum())) if (pnl<0).any() else None,'win_rate':float((pnl>0).mean()),'stop_rate':float(h.stop.mean()),'worst_trade':float(pnl.min()),'peak_planned_loss':float(h.planned_loss.sum())})
 pd.DataFrame(scale).to_csv(OUT/'scale_in_by_underlying_state.csv',index=False)
 print(pd.DataFrame(q).to_string(index=False))
if __name__=='__main__':main()
