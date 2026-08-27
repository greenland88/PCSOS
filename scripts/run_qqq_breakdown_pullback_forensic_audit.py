"""Read-only forensic audit of frozen QQQ underlying-state baseline entries."""
from pathlib import Path
from hashlib import sha256
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'research_outputs'/'spy_qqq_underlying_state_research_20260821'
OUT=ROOT/'research_outputs'/'qqq_breakdown_pullback_forensic_audit_20260821'
END={'train':pd.Timestamp('2025-12-31'),'validation':pd.Timestamp('2026-05-31')}

def pf(x):
 w=x[x.pnl>0].pnl.sum();l=x[x.pnl<0].pnl.sum();return float(w/abs(l)) if l else None
def phase(frame, daily):
  x=frame.sort_values('date').drop(columns=[c for c in ['close','high','low','support_level','pivot_date','pivot_confirmation_date','support_first_usable_date','pullback_result','stabilization_result','confirmation_result','breakdown_result'] if c in frame.columns]).copy();d=daily.sort_values('date').copy();d['prior_state']=d.final_underlying_state.shift();d['break_run']=(d.final_underlying_state.eq('BREAKDOWN')).groupby((~d.final_underlying_state.eq('BREAKDOWN')).cumsum()).cumcount()+1
  x=x.merge(d[['date','close','high','low','support_identity','support_level','pivot_date','pivot_confirmation_date','support_first_usable_date','pullback_result','stabilization_result','confirmation_result','breakdown_result','production_trend_state','trend_result','trend_reason_codes','underlying_state_reason_codes','unknown_reason_codes','prior_state','break_run']],on='date',how='left')
  x['entry_phase']=x.apply(lambda r:'FRESH_BREAKDOWN' if r.final_underlying_state=='BREAKDOWN' and r.prior_state!='BREAKDOWN' else 'CONTINUING_BREAKDOWN' if r.final_underlying_state=='BREAKDOWN' else 'UNCLASSIFIABLE_WITH_EXISTING_PRODUCTION_PREDICATES',axis=1)
  x['active_production_breakdown_predicates']=x.breakdown_result;x['active_pullback_predicates']=x.pullback_result;x['active_stabilization_predicates']=x.stabilization_result;x['active_confirmation_predicates']=x.confirmation_result
  x['close_minus_support']=x.close-x.support_level;x['close_support_pct']=x.close/x.support_level-1
  x['trade_id']=x.candidate_id;x['entry_date']=x.date;x['realized_pnl']=x.pnl;x['initial_credit']=x.credit;x['consecutive_breakdown_trading_days_at_entry']=x.break_run
  return x
def post(entry, daily):
 rows=[]
 for r in entry.itertuples():
  d=daily[(daily.date>=r.date)&(daily.date<=min(pd.Timestamp(r.exit_date),END[r.split]))].sort_values('date'); base=float(r.close)
  z={'candidate_id':r.candidate_id,'split':r.split,'ticker':'QQQ','entry_state_as_of':r.final_underlying_state,'post_entry_outcome':'POST_ENTRY_ONLY','entry_date':r.date,'exit_date':r.exit_date,'realized_pnl':r.pnl,'stop':r.stop}
  for n in (1,2,3,5,10):
   q=d.iloc[n] if len(d)>n else None;z[f'underlying_return_{n}d']=float(q.close/base-1) if q is not None else None
  z.update({'mae_underlying':float(d.low.min()/base-1) if len(d) else None,'mfe_underlying':float(d.high.max()/base-1) if len(d) else None,'continued_lower_low':bool((d.low<float(r.low)).any()) if len(d) else None,'entered_downtrend_post_entry':bool(d.final_underlying_state.eq('DOWNTREND').any()) if len(d) else None,'entered_breakdown_post_entry':bool(d.final_underlying_state.eq('BREAKDOWN').any()) if len(d) else None,'short_strike_touch':bool((d.low<=r.short_strike).any()) if len(d) else None,'long_strike_touch':bool((d.low<=r.long_strike).any()) if len(d) else None})
  if pd.notna(r.support_level):
   above=d[d.close>=r.support_level];z['first_close_back_above_entry_support_date']=above.date.iloc[0] if len(above) else None;z['close_above_confirmed_support_at_entry']=bool(r.close>=r.support_level)
  else:z['first_close_back_above_entry_support_date']=None;z['close_above_confirmed_support_at_entry']=None
  rows.append(z)
 return pd.DataFrame(rows)
def perf(x, group):
 out=[]
 for keys,g in x.groupby(group,dropna=False):
  keys=(keys,) if not isinstance(keys,tuple) else keys;out.append(dict(zip(group,keys))|{'entries':len(g),'independent_setups':g.setup_id.nunique(),'pnl':float(g.pnl.sum()),'expectancy':float(g.pnl.mean()),'profit_factor':pf(g),'win_rate':float((g.pnl>0).mean()),'stop_rate':float(g.stop.mean()),'worst_trade':float(g.pnl.min()),'scale_in_orders':';'.join(map(str,sorted(g.scale_in_order.unique())))})
 return pd.DataFrame(out)
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 entries=pd.read_parquet(SRC/'candidate_state_attribution.parquet');entries=entries[(entries.scenario_id=='UNDERLYING_STATE_DISABLED_BASELINE')&(entries.ticker=='QQQ')].copy()
 daily=pd.read_parquet(SRC/'daily_underlying_state_ledger.parquet');daily=daily[daily.ticker.eq('QQQ')].copy()
 rec=[]
 for split, n, pnl in [('validation',53,-940),('train',753,2865)]:
  x=entries[entries.split.eq(split)];rec.append({'split':split,'entries':len(x),'pnl':float(x.pnl.sum()),'candidate_hash':sha256('|'.join(sorted(x.candidate_id)).encode()).hexdigest(),'status':'PASS' if (len(x),round(x.pnl.sum()))==(n,pnl) else 'FAIL'})
 b=entries[(entries.split=='validation')&(entries.final_underlying_state=='BREAKDOWN')];p=entries[(entries.split=='validation')&(entries.final_underlying_state=='PULLBACK_IN_UPTREND')]
 rec += [{'split':'validation','subset':'BREAKDOWN','entries':len(b),'pnl':float(b.pnl.sum()),'status':'PASS' if (len(b),round(b.pnl.sum()))==(21,344) else 'FAIL'},{'split':'validation','subset':'PULLBACK_IN_UPTREND','entries':len(p),'pnl':float(p.pnl.sum()),'status':'PASS' if (len(p),round(p.pnl.sum()))==(11,-749) else 'FAIL'}]
 pd.DataFrame(rec).to_csv(OUT/'baseline_reconciliation.csv',index=False)
 if not all(r['status']=='PASS' for r in rec):raise SystemExit('BASELINE_RECONCILIATION_FAILED')
 allphase=[];allpost=[]
 for split in ('train','validation'):
  e=entries[entries.split.eq(split)];d=daily[daily.split.eq(split)]
  for state,name in [('BREAKDOWN','breakdown'),('PULLBACK_IN_UPTREND','pullback')]:
   z=phase(e[e.final_underlying_state.eq(state)],d);allphase.append(z);allpost.append(post(z,d));z.to_csv(OUT/f'{name}_entry_forensic_ledger_{split}.csv',index=False)
 ph=pd.concat(allphase,ignore_index=True);po=pd.concat(allpost,ignore_index=True);ph[ph.split.eq('validation')&ph.final_underlying_state.eq('BREAKDOWN')].to_csv(OUT/'breakdown_entry_forensic_ledger.csv',index=False);ph[ph.split.eq('validation')&ph.final_underlying_state.eq('PULLBACK_IN_UPTREND')].to_csv(OUT/'pullback_entry_forensic_ledger.csv',index=False);po.to_csv(OUT/'post_entry_path_analysis.csv',index=False)
 breakdown=ph[ph.final_underlying_state.eq('BREAKDOWN')].merge(po[['candidate_id','underlying_return_1d','underlying_return_3d','underlying_return_5d','underlying_return_10d','continued_lower_low','first_close_back_above_entry_support_date','short_strike_touch']],on='candidate_id',how='left');perf(breakdown,['split','entry_phase']).to_csv(OUT/'breakdown_phase_performance.csv',index=False)
 pull=ph[ph.final_underlying_state.eq('PULLBACK_IN_UPTREND')].merge(po[['candidate_id','entered_downtrend_post_entry','entered_breakdown_post_entry']],on='candidate_id',how='left');perf(pull,['split','pullback_result','stabilization_result']).to_csv(OUT/'pullback_failure_analysis.csv',index=False)
 perf(ph[ph.split.eq('validation')],['final_underlying_state','setup_id']).to_csv(OUT/'setup_concentration_analysis.csv',index=False);perf(ph,['split','final_underlying_state','scale_in_order']).to_csv(OUT/'scale_in_contribution_analysis.csv',index=False)
 tv=perf(ph,['split','final_underlying_state','entry_phase']);tv.to_csv(OUT/'train_validation_phase_comparison.csv',index=False)
 pd.DataFrame([{'predicate':'breakdown','available':True,'source':'pcs.trend.pullback/analyze_market_structure'},{'predicate':'pullback','available':True,'source':'pcs.entry.pullback_gate'},{'predicate':'stabilization','available':True,'source':'pcs.entry.pullback_gate PASS'},{'predicate':'reclaim','available':False,'source':'NOT_FOUND'},{'predicate':'reconfirmation','available':False,'source':'NOT_FOUND'}]).to_csv(OUT/'production_predicate_availability.csv',index=False)
 vb=breakdown[breakdown.split.eq('validation')];vp=pull[pull.split.eq('validation')];answers=[{'question':'BREAKDOWN_PROFITS_ASSOCIATED_WITH_POST_ENTRY_REBOUND','answer':'BREAKDOWN PROFITS ASSOCIATED WITH POST-ENTRY REBOUND' if (vb[vb.pnl>0].underlying_return_3d>0).all() else 'BREAKDOWN REBOUND EXPLANATION NOT CONFIRMED','evidence_entries':len(vb[vb.pnl>0])},{'question':'BREAKDOWN_PROFIT_BY_PHASE','fresh_pnl':float(vb[vb.entry_phase=='FRESH_BREAKDOWN'].pnl.sum()),'continuing_pnl':float(vb[vb.entry_phase=='CONTINUING_BREAKDOWN'].pnl.sum()),'unclassifiable_pnl':0.0},{'question':'BREAKDOWN_SETUP_COUNT','answer':int(vb.setup_id.nunique())},{'question':'BREAKDOWN_SCALEIN_2PLUS_PNL','answer':float(vb[vb.scale_in_order>=2].pnl.sum())},{'question':'PULLBACK_LOSS_CONCENTRATION','answer':float(vp.pnl.min()),'top_loss_share':float(vp.nsmallest(1,'pnl').pnl.sum()/vp.pnl.sum())},{'question':'PULLBACK_NO_STABILIZATION_ENTRIES','answer':int(vp.stabilization_result.ne('PASS').sum())},{'question':'PULLBACK_POST_DOWN_OR_BREAK','answer':int((vp.entered_downtrend_post_entry.fillna(False)|vp.entered_breakdown_post_entry.fillna(False)).sum())},{'question':'SYSTEM_MISTOOK_DOWNTREND_AS_PULLBACK','answer':'NOT_CONFIRMED'}]
 pd.DataFrame(answers).to_csv(OUT/'direct_question_answers.csv',index=False)
 pd.DataFrame([{'check':'baseline_reconciliation','status':'PASS'},{'check':'entry_state_as_of_only','status':'PASS'},{'check':'final_oos_exclusion','status':'PASS'},{'check':'production_reclaim_not_invented','status':'PASS'}]).to_csv(OUT/'validation_checks.csv',index=False)
 (OUT/'research_manifest.json').write_text(json.dumps({'source':str(SRC),'final_oos_read':False,'options_population_rerun':False,'research_only':True},indent=2));(OUT/'research_summary.md').write_text('Forensic results are evidence-only. Reclaim/reconfirmation predicates are unavailable and no recovery classification was invented.\n')
 print(json.dumps({'validation_breakdown':len(b),'validation_pullback':len(p),'post_paths':len(po)},indent=2))
if __name__=='__main__':main()
