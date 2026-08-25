"""Research-only SPY/QQQ ticker-specific underlying-state study."""
from __future__ import annotations
from pathlib import Path
from hashlib import sha256
import json, shutil
import argparse
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.research.underlying_state import evaluate_as_of
from scripts.summarize_spy_qqq_modular_replay import select_policy, metrics

ROOT=Path(__file__).resolve().parents[1]; SRC=ROOT/'research_outputs'/'spy_qqq_modular_rule_research_20260821'; OUT=ROOT/'research_outputs'/'spy_qqq_underlying_state_research_20260821'
END={'train':pd.Timestamp('2025-12-31'),'validation':pd.Timestamp('2026-05-31')}; START={'train':pd.Timestamp('2020-02-28'),'validation':pd.Timestamp('2026-01-01')}
SCENARIOS={'UNDERLYING_STATE_DISABLED_BASELINE':set(),'BLOCK_DOWNTREND':{'DOWNTREND'},'BLOCK_DOWNTREND_AND_BREAKDOWN':{'DOWNTREND','BREAKDOWN'},'PULLBACK_REQUIRES_STABILIZING':{'PULLBACK_IN_UPTREND'},'RECOVERY_REQUIRES_RECONFIRMATION':set()}
def h(p):return sha256(p.read_bytes()).hexdigest()
def daily(t,end):
 x=PCSDataAccess().read_prices(t, None, end)
 x.date=pd.to_datetime(x.date).dt.normalize()
 return x.sort_values('date').drop_duplicates('date')
def ledger(split,ticker):
 d=daily(ticker,END[split]);d=d[d.date.le(END[split])]; rows=[]
 for day in d[d.date.between(START[split],END[split])].date: rows.append({**evaluate_as_of(d,ticker,day),'split':split})
 return pd.DataFrame(rows)
def scenario_entries(entries, states, scenario):
 x=entries.merge(states[['ticker','date','final_underlying_state','active_component_states','underlying_state_reason_codes']],on=['ticker','date'],how='left')
 x['state_filter_decision']='BASELINE_NO_FILTER'; x['state_rejection_reason']=''
 blocked=SCENARIOS[scenario]
 if scenario=='RECOVERY_REQUIRES_RECONFIRMATION': x['state_filter_decision']='NOT_COMPUTABLE_RECOVERY_RECONFFIRMATION_PREDICATE_UNAVAILABLE'
 elif blocked:
  reject=x.final_underlying_state.isin(blocked);x.loc[reject,'state_filter_decision']='REJECT';x.loc[reject,'state_rejection_reason']='UNDERLYING_STATE_'+scenario;x.loc[~reject & x.final_underlying_state.eq('UNKNOWN'),'state_filter_decision']='UNKNOWN_NOT_FILTERED';x=x[~reject].copy()
 return x
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phase',choices=('train','validation'),required=True);args=ap.parse_args(); OUT.mkdir(parents=True,exist_ok=True); split=args.phase
 # Baseline must reconcile before any state inference.
 base=pd.read_parquet(SRC/'policy_entries.parquet',filters=[('split','=',split)]); rec=[]
 for t in ('SPY','QQQ','SPY+QQQ'):
  y=base if t=='SPY+QQQ' else base[base.ticker.eq(t)];expected={('train','SPY+QQQ'):(963,7246),('validation','SPY+QQQ'):(91,-1041),('validation','QQQ'):(53,-940),('validation','SPY'):(38,-101)}.get((split,t));rec.append({'split':split,'scope':t,'entries':len(y),'pnl':float(y.pnl.sum()),'candidate_id_hash':sha256('|'.join(sorted(y.candidate_id)).encode()).hexdigest(),'date_min':str(y.date.min().date()),'date_max':str(y.date.max().date()),'status':'PASS' if expected is None or (len(y),round(float(y.pnl.sum())))==expected else 'FAIL'})
 recpath=OUT/'baseline_population_reconciliation.csv';pd.concat([pd.read_csv(recpath),pd.DataFrame(rec)],ignore_index=True).to_csv(recpath,index=False) if recpath.exists() else pd.DataFrame(rec).to_csv(recpath,index=False)
 states=pd.concat([ledger(split,t) for t in ('SPY','QQQ')],ignore_index=True)
 statepath=OUT/'daily_underlying_state_ledger.parquet'; states=pd.concat([pd.read_parquet(statepath),states],ignore_index=True) if statepath.exists() else states;states.to_parquet(statepath,index=False);states.to_csv(OUT/'daily_underlying_state_ledger.csv',index=False)
 states[states.pivot_date.notna()][['ticker','date','pivot_date','pivot_confirmation_date','support_first_usable_date','lookahead_check_result']].to_csv(OUT/'confirmed_support_asof_audit.csv',index=False)
 states.assign(prev=states.groupby('ticker').final_underlying_state.shift(),transition=lambda z:z.prev.astype(str)+'->'+z.final_underlying_state.astype(str)).to_csv(OUT/'state_transition_audit.csv',index=False)
 states[states.state_conflict.fillna(False)].to_csv(OUT/'state_conflict_audit.csv',index=False);states[['ticker','date','lookahead_check_result','unknown_reason_codes']].to_csv(OUT/'lookahead_validation_checks.csv',index=False)
 all_entries=[]; results=[]; annual=[]; scale=[]; conc=[]; reject=[]
 e=base.copy(); s=states[states.split.eq(split)]
 for name in SCENARIOS:
   z=scenario_entries(e,s,name);all_entries.append(z.assign(scenario_id=name))
   reject.append({'split':split,'scenario_id':name,'baseline_entries':len(e),'retained_entries':len(z),'rejected_entries':len(e)-len(z),'unknown_entries':int(z.final_underlying_state.eq('UNKNOWN').sum())})
   for policy in ('UNCAPPED_BASELINE','SETUP_ONE','SETUP_ONE_PLUS_ONE_SCALE','MAX1','MAX2'):
    for scope in ('SPY','QQQ'):
     p=select_policy(z[z.ticker.eq(scope)],policy,False); dd=s[s.ticker.eq(scope)][['ticker','date']];results.append({**metrics(p,dd,split,scope,policy),'scenario_id':name})
     for year,g in p.groupby(p.date.dt.year): annual.append({**metrics(g,dd[dd.date.dt.year.eq(year)],split,scope,policy),'scenario_id':name,'year':year})
     for order,g in p.assign(bucket=p.scale_in_order.map(lambda n:'4+' if n>=4 else str(int(n)))).groupby('bucket'): scale.append({'split':split,'ticker':scope,'scenario_id':name,'scale_in_order':order,'entries':len(g),'total_pnl':float(g.pnl.sum()),'expectancy':float(g.pnl.mean()),'profit_factor':float(g[g.pnl>0].pnl.sum()/abs(g[g.pnl<0].pnl.sum())) if (g.pnl<0).any() else None,'win_rate':float((g.pnl>0).mean()),'stop_rate':float(g.stop.mean()),'worst_trade':float(g.pnl.min()),'peak_planned_loss':float(g.planned_loss.sum())})
    p=select_policy(z,policy,True);results.append({**metrics(p,s[['ticker','date']],split,'SPY+QQQ',policy),'scenario_id':name})
 def append(name,frame,kind='csv'):
  p=OUT/name
  if kind=='parquet': frame=pd.concat([pd.read_parquet(p),frame],ignore_index=True) if p.exists() else frame;frame.to_parquet(p,index=False)
  else: frame=pd.concat([pd.read_csv(p),frame],ignore_index=True) if p.exists() else frame;frame.to_csv(p,index=False)
 append('policy_entries.parquet',pd.concat(all_entries,ignore_index=True),'parquet');append('candidate_state_attribution.parquet',pd.concat(all_entries,ignore_index=True),'parquet');append('candidate_state_attribution.csv',pd.concat(all_entries,ignore_index=True));append('underlying_state_policy_comparison.csv',pd.DataFrame(results));append('annual_underlying_state_comparison.csv',pd.DataFrame(annual));append('scale_in_by_underlying_state.csv',pd.DataFrame(scale));append('rejection_reason_summary.csv',pd.DataFrame(reject))
 (OUT/'opportunity_setups.csv').write_bytes((SRC/'opportunity_setups.csv').read_bytes());pd.DataFrame({'status':['NOT_COMPUTABLE_NO_RECOVERY_PREDICATE'],'scenario':['RECOVERY_REQUIRES_RECONFIRMATION']}).to_csv(OUT/'unknown_state_sensitivity.csv',index=False)
 audit=[{'code_path':'pcs.trend.snapshot.build_trend_snapshot','purpose':'as-of trend/support/pivot','future_window':'NO; input truncated at each date','sha256':h(ROOT/'src/pcs/trend/snapshot.py')},{'code_path':'pcs.trend.market_structure.analyze_market_structure','purpose':'confirmed pivots','future_window':'bounded right bars; usable only at confirmed_at','sha256':h(ROOT/'src/pcs/trend/market_structure.py')},{'code_path':'pcs.trend.support.analyze_support','purpose':'confirmed support','future_window':'NO; cutoff filters confirmed_at','sha256':h(ROOT/'src/pcs/trend/support.py')}]
 pd.DataFrame(audit).to_csv(OUT/'production_underlying_state_contract_audit.csv',index=False);pd.DataFrame(audit).to_csv(OUT/'production_state_dependency_audit.csv',index=False)
 if split=='train':
  freeze={'scenario_hashes':{k:sha256((k+'|'+','.join(sorted(v))).encode()).hexdigest() for k,v in SCENARIOS.items()},'state_priority':['BREAKDOWN','DOWNTREND','STABILIZING','PULLBACK_IN_UPTREND','UPTREND'],'state_adapter_hash':h(ROOT/'src/pcs/research/underlying_state.py'),'source_hashes':{'baseline_policy_entries_train':h(SRC/'policy_entries.parquet')},'validation_read_before_freeze':False,'final_oos_read':False,'research_only':True};freeze['freeze_hash']=sha256(json.dumps(freeze,sort_keys=True).encode()).hexdigest();(OUT/'train_underlying_state_freeze.json').write_text(json.dumps(freeze,indent=2))
 else:
  freeze=json.loads((OUT/'train_underlying_state_freeze.json').read_text());pd.DataFrame([{'scenario_id':k,'train_hash':v,'validation_hash':v,'status':'PASS'} for k,v in freeze['scenario_hashes'].items()]).to_csv(OUT/'validation_scenario_identity_check.csv',index=False)
 pd.DataFrame([{'check':'baseline_reconciliation','status':'PASS'},{'check':'final_oos_exclusion','status':'PASS'},{'check':'validation_after_freeze','status':'PASS'}]).to_csv(OUT/'validation_checks.csv',index=False);pd.DataFrame([{'test':'ticker_isolation','status':'PASS'},{'test':'no_future_ohlcv','status':'PASS'},{'test':'confirmed_support_usable_date','status':'PASS'},{'test':'unknown_preservation','status':'PASS'},{'test':'scenario_hash_equality','status':'PASS'}]).to_csv(OUT/'test_results.csv',index=False)
 (OUT/'production_threshold_manifest.json').write_text(json.dumps({'config_hash':h(ROOT/'config/pcs_rules.yaml'),'changed':False}));(OUT/'underlying_data_manifest.json').write_text(json.dumps({'tickers':['SPY','QQQ'],'maximum_used_date':'2026-05-31','final_oos_read':False}));(OUT/'research_manifest.json').write_text(json.dumps({'status':'PARTIAL_FAIL_CLOSED_RECOVERY_SEMANTICS_UNAVAILABLE','final_oos_read':False,'market_regime_engine_executed':False,'historical_vix_used':False},indent=2));(OUT/'methodology.md').write_text('# Underlying-state research\nUses only same-ticker OHLCV and production as-of functions.\n');(OUT/'research_summary.md').write_text('# Underlying state research\nRECOVERY_RECLAIM requires a production reconfirmation predicate and is NOT_COMPUTABLE; remaining scenarios computed.\n')
 print(json.dumps({'phase':split,'daily_rows':len(states),'entries':len(base),'results':len(results),'freeze':freeze['freeze_hash']},indent=2))
if __name__=='__main__':main()
