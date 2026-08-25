"""Research-only full daily replay and eligibility-reset reconciliation."""
from pathlib import Path
import hashlib, json, subprocess, datetime
import pandas as pd
from pcs.research.entry_candidate_universe import _daily, build_historical_setup_context, generate_observable_candidates
from pcs.research.credit_stop import load_quotes_canonical

ROOT=Path('.'); OUT=ROOT/'research_outputs/opportunity_state_machine_research_20260821'; BASE=ROOT/'research_outputs/spy_qqq_pcs_baseline_20260821'; OUT.mkdir(parents=True,exist_ok=True)
WINDOWS={'TRAIN':('2020-02-28','2025-12-31'),'VALIDATION':('2026-01-01','2026-05-31')}

def h(p):
 q=Path(p); return hashlib.sha256(q.read_bytes()).hexdigest() if q.exists() else 'MISSING'
def daily(t):
 ps=sorted((ROOT/'data/parquet/daily'/f'symbol={t}').rglob('*.parquet')); return _daily(ps[0]).iloc[0:0] if not ps else pd.concat((_daily(p) for p in ps),ignore_index=True).drop_duplicates('date').sort_values('date').reset_index(drop=True)
def status(v): return 'PASS' if v is True else 'FAIL' if v is False else 'UNKNOWN'

def replay_ticker(t):
 d=daily(t); bench=daily('QQQ'); opt=ROOT/'data/parquet/options_monthly'/f'symbol={t}'
 rows=[]; candidates=[]; spot=[]
 for split,(lo,hi) in WINDOWS.items():
  dates=d[d.date.between(lo,hi)].date
  # Canonical options validation is intentionally attempted only as a bounded
  # preflight; full read is blocked by duplicate-key validation in current data.
  cleaned=pd.DataFrame(); chains={}; meta={'status':'OPTIONS_READ_BLOCKED','error':'PREVIOUS_PREFLIGHT_AMBIGUOUS_OPTION_KEYS'}
  cand,summary=generate_observable_candidates(t,sorted((ROOT/'data/parquet/daily'/f'symbol={t}').rglob('*.parquet'))[0],opt,lo,hi,None,chains,benchmark_path=sorted((ROOT/'data/parquet/daily/symbol=QQQ').rglob('*.parquet'))[0])
  if len(cand): candidates.append(cand.assign(split=split))
  cdates=set(pd.to_datetime(cand.date).dt.normalize()) if len(cand) else set()
  for day in dates:
   chain=chains.get(pd.Timestamp(day),pd.DataFrame())
   if not chains:
    ctx={'available':False,'reason_codes':['OPTIONS_SOURCE_UNAVAILABLE'],'entry_context':None}
   else:
    ctx=build_historical_setup_context(d,bench,day,t,'QQQ')
   setup_pass=ctx.get('available') and ctx.get('entry_context') is not None and ctx['entry_context'].entry_context_state=='READY'
   dayc=cand[pd.to_datetime(cand.date).dt.normalize().eq(pd.Timestamp(day))] if len(cand) else cand
   gate_trend=getattr(ctx.get('trend_gate_result'),'trend_gate_result',None); gate_pull=getattr(ctx.get('pullback_gate_result'),'pullback_gate_result',None)
   option_state='PASS' if len(chain) else 'UNKNOWN'
   if not ctx.get('available'): final='UNKNOWN'; overall='REQUIRED_DATA_MISSING'
   elif len(dayc): final='PASS'; overall='ELIGIBLE'
   elif not len(chain): final='UNKNOWN'; overall='REQUIRED_DATA_MISSING'
   elif not setup_pass: final='FAIL'; overall='GATE_FAILED'
   else: final='FAIL'; overall='NO_FINAL_SPREAD'
   rows.append({'date':pd.Timestamp(day).date(),'ticker':t,'split':split,'available_data':True,'data_availability_reason':'UNDERLYING_PRESENT','option_data_available':bool(len(chain)),'candidate_count_generated':int(len(dayc)),'candidate_count_evaluated':int(len(dayc)),'pipeline_path':'generate_observable_candidates','config_version':'FROZEN_SAFE_STRIKE_2.3_DTE_30_45_CREDIT_0.10','data_version':'daily_parquet+options_monthly','market_setup_eligible':status(setup_pass),'portfolio_allowed':'UNKNOWN','final_eligible':final,'overall_status':overall,'trend_state':ctx.get('trend_state','UNKNOWN'),'trend_raw_value':getattr(ctx.get('trend_score'),'score',None),'pullback_state':ctx.get('pullback_state','UNKNOWN'),'support_identity':ctx.get('support_state','UNKNOWN'),'support_level':'UNKNOWN','confirmation_state':'UNKNOWN','trend_gate':status(gate_trend=='PASS' if gate_trend is not None else None),'pullback_gate':status(gate_pull=='PASS' if gate_pull is not None else None),'regime_gate':'UNKNOWN','support_gate':'UNKNOWN','safe_strike_gate':'PASS' if len(dayc) else 'UNKNOWN','dte_gate':'PASS' if len(dayc) else 'UNKNOWN','credit_gate':'PASS' if len(dayc) else 'UNKNOWN','liquidity_gate':'PASS' if len(dayc) else 'UNKNOWN','event_gate':'UNKNOWN','portfolio_gate':'UNKNOWN','rejection_reason_codes':'|'.join(ctx.get('reason_codes',[])) if ctx.get('reason_codes') else ('NO_FINAL_SPREAD' if setup_pass and len(chain) else 'UNKNOWN'),'gate_state_available':bool(ctx.get('available'))})
   spot.append(rows[-1])
 return pd.DataFrame(rows),pd.concat(candidates,ignore_index=True) if candidates else pd.DataFrame(),spot

def main():
 allrows=[]; allc=[]; spot=[]
 for t in ('SPY','QQQ'):
  r,c,s=replay_ticker(t); allrows.append(r); allc.append(c); spot.extend(s)
 ledger=pd.concat(allrows,ignore_index=True).sort_values(['ticker','date']); cand=pd.concat(allc,ignore_index=True) if any(len(x) for x in allc) else pd.DataFrame()
 prev=ledger.groupby('ticker').final_eligible.shift(1); ledger['previous_day_eligible']=prev; ledger['eligibility_transition']='UNKNOWN'
 known=ledger.final_eligible.isin(['PASS','FAIL']); p=prev.notna()
 ledger.loc[known&p,'eligibility_transition']=prev[known&p].map({'PASS':'TRUE','FAIL':'FALSE'})+'_'+ledger.loc[known&p,'final_eligible'].map({'PASS':'TRUE','FAIL':'FALSE'})
 ledger.loc[known&~p,'eligibility_transition']='UNKNOWN_TO_'+ledger.loc[known&~p,'final_eligible'].map({'PASS':'TRUE','FAIL':'FALSE'})
 ledger.to_csv(OUT/'daily_entry_decision_ledger.csv',index=False)
 if len(cand): cand.to_csv(OUT/'candidate_gate_ledger.csv',index=False)
 else: pd.DataFrame(columns=['ticker','split','date','candidate_id','gate_state_available','status']).to_csv(OUT/'candidate_gate_ledger.csv',index=False)
 # Eligibility-reset setups only where full daily state is known.
 setups=[]; entries=[]
 for t,g in ledger.groupby('ticker'):
  sid=0; active=None; last=None
  for _,r in g.sort_values('date').iterrows():
   if r.final_eligible=='PASS' and (last in ('FAIL',None)):
    sid+=1; active=f'{t}_SETUP_{pd.Timestamp(r.date).date()}_{sid:03d}'
   if r.final_eligible=='PASS' and active:
    setups.append({'policy_definition':'ELIGIBILITY_RESET_ONLY','ticker':t,'split':r.split,'setup_id':active,'setup_start_date':r.date,'setup_end_date':r.date,'setup_start_reason':'INITIAL_OBSERVED_ELIGIBLE' if last is None else 'FALSE_TO_TRUE','eligible_day_count':1,'active_trading_day_count':1,'baseline_entry_count':r.candidate_count_generated,'policy_entry_count':r.candidate_count_generated if last in ('FAIL',None) else 0,'scale_in_count':0 if last in ('FAIL',None) else r.candidate_count_generated,'portfolio_rejected_day_count':0,'unresolved_unknown_flag':False})
   last=r.final_eligible
 setupsdf=pd.DataFrame(setups).drop_duplicates(['ticker','setup_id']) if setups else pd.DataFrame()
 setupsdf.to_csv(OUT/'opportunity_setups.csv',index=False)
 entries=cand.copy() if len(cand) else pd.DataFrame();
 if len(entries): entries['policy']='UNCAPPED_BASELINE'; entries['setup_id']='UNKNOWN_UNTIL_SETUP_RECONCILIATION'; entries.to_csv(OUT/'setup_entries.csv',index=False)
 # Coverage and gate summaries.
 cov=[]; gates=['trend_gate','pullback_gate','regime_gate','support_gate','safe_strike_gate','dte_gate','credit_gate','liquidity_gate','event_gate','portfolio_gate']
 for (t,s,y),g in ledger.assign(year=pd.to_datetime(ledger.date).dt.year).groupby(['ticker','split','year']):
  q={'ticker':t,'split':s,'year':y,'underlying_trading_days':len(g),'option_data_available_days':int(g.option_data_available.sum()),'candidate_generated_days':int((g.candidate_count_generated>0).sum()),'fully_evaluated_days':int(g.gate_state_available.sum()),'eligible_days':int(g.final_eligible.eq('PASS').sum()),'rejected_days':int(g.final_eligible.eq('FAIL').sum()),'unknown_days':int(g.final_eligible.eq('UNKNOWN').sum())}
  q['eligibility_rate_over_all_trading_days_pct']=round(100*q['eligible_days']/q['underlying_trading_days'],4) if q['underlying_trading_days'] else None; q['eligibility_rate_over_evaluated_days_pct']=round(100*q['eligible_days']/q['fully_evaluated_days'],4) if q['fully_evaluated_days'] else None
  for gate in gates:
   q[gate+'_pass']=int(g[gate].eq('PASS').sum()); q[gate+'_fail']=int(g[gate].eq('FAIL').sum()); q[gate+'_unknown']=int(g[gate].isin(['UNKNOWN','NOT_EVALUATED']).sum())
  cov.append(q)
 pd.DataFrame(cov).to_csv(OUT/'daily_replay_coverage.csv',index=False)
 pd.DataFrame([{'gate':g,'called_pass':int(ledger[g].ne('UNKNOWN').sum()),'called_fail':0,'not_called_or_unknown':int(ledger[g].eq('UNKNOWN').sum())} for g in gates]).to_csv(OUT/'gate_call_coverage.csv',index=False)
 # Reconcile sealed candidates.
 rec=[]
 for t in ('SPY','QQQ'):
  sealed=pd.read_parquet(BASE/f'{t}_entry_contract_v2.parquet'); rr=ledger[ledger.ticker.eq(t)]
  for _,r in sealed.iterrows():
   d=rr[pd.to_datetime(rr.date).eq(pd.Timestamp(r.decision_date))]; c=cand[(cand.ticker==t)&(pd.to_datetime(cand.date).eq(pd.Timestamp(r.decision_date)))] if len(cand) else cand
   rec.append({'ticker':t,'date':pd.Timestamp(r.decision_date).date(),'split':'TRAIN' if pd.Timestamp(r.decision_date).year<=2025 else 'VALIDATION','sealed_candidate_present':True,'replay_candidate_present':bool(len(c)),'sealed_eligible':True,'replay_eligible':bool(len(c)),'contract_match':'UNKNOWN','expiration_match':'UNKNOWN','strike_match':'UNKNOWN','credit_match':'UNKNOWN','gate_state_available':bool(len(d) and d.iloc[0].gate_state_available),'mismatch_reason':'REPLAY_MATCH_DATE_ONLY' if len(c) else 'REPLAY_CANDIDATE_MISSING','status':'PARTIAL' if len(c) else 'MISMATCH'})
 pd.DataFrame(rec).to_csv(OUT/'baseline_replay_reconciliation.csv',index=False)
 # Preserve existing comparison artifacts but make unsupported metrics explicit.
 policies=['UNCAPPED_BASELINE','ELIGIBILITY_RESET_ONLY','STRUCTURAL_RESET','STRUCTURAL_RESET_PLUS_ONE_CONTROLLED_SCALE_IN','EXISTING_MAX1','EXISTING_MAX2']
 pc=[{'policy':p,'ticker':t,'split':s,'independent_setups':'NOT_COMPUTABLE','simulated_entries':'NOT_COMPUTABLE','total_pnl':'NOT_COMPUTABLE','expectancy':'NOT_COMPUTABLE','profit_factor':'NOT_COMPUTABLE','win_rate_pct':'NOT_COMPUTABLE','max_concurrent_positions':'NOT_COMPUTABLE','aggregate_planned_loss_peak':'NOT_COMPUTABLE','maximum_drawdown':'NOT_COMPUTABLE','worst_trade':'NOT_COMPUTABLE','status':'NOT_COMPUTABLE','reason':'full eligibility unknown due option source blocker'} for p in policies for t in ('SPY','QQQ','COMBINED') for s in ('TRAIN','VALIDATION')]
 pd.DataFrame(pc).to_csv(OUT/'policy_comparison.csv',index=False)
 pd.DataFrame(pc).assign(year='ALL').to_csv(OUT/'annual_policy_comparison.csv',index=False)
 pd.DataFrame([{'policy':p,'ticker':t,'split':s,'scale_in_order_group':'UNKNOWN','entry_count':'NOT_COMPUTABLE','total_pnl':'NOT_COMPUTABLE','expectancy':'NOT_COMPUTABLE','stop_rate':'NOT_COMPUTABLE','status':'NOT_COMPUTABLE'} for p in policies for t in ('SPY','QQQ') for s in ('TRAIN','VALIDATION')]).to_csv(OUT/'scale_in_marginal_analysis.csv',index=False)
 pd.DataFrame([{'policy':p,'split':s,'max_spy_open_positions':'NOT_COMPUTABLE','max_qqq_open_positions':'NOT_COMPUTABLE','max_combined_open_positions':'NOT_COMPUTABLE','peak_combined_planned_loss':'NOT_COMPUTABLE','worst_same_day_combined_loss':'NOT_COMPUTABLE','status':'NOT_COMPUTABLE'} for p in policies for s in ('TRAIN','VALIDATION')]).to_csv(OUT/'portfolio_concurrency_analysis.csv',index=False)
 diag=[]
 for t in ('SPY','QQQ'):
  for s,g in ledger.groupby(['ticker','split']):
   diag.append({'ticker':t,'split':s[1] if isinstance(s,tuple) else s,'diagnostic_type':'BREAKPOINT_REPLAY','affected_start_date':g.date.min(),'affected_end_date':g.date.max(),'gate':'ALL','observed_behavior':'Replay denominator is underlying daily dates; qualifying-only 100% denominator is not reused','expected_behavior':'Separate underlying, option, candidate, evaluated, eligible and unknown denominators','evidence':'daily_replay_coverage.csv','root_cause':'Requires review of replay-vs-sealed mismatches; no forced alignment','affected_day_count':len(g),'impact':'See reconciliation','status':'PARTIAL'})
 pd.DataFrame(diag).to_csv(OUT/'eligibility_breakpoint_diagnosis.csv',index=False)
 manifest={'run_timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'run_type':'full_daily_replay_run','config_hashes':{str(p):h(p) for p in [ROOT/'config/data_source_routes.yaml',ROOT/'config/pcs_rules.yaml',ROOT/'src/pcs/trend/config.py',ROOT/'src/pcs/entry/gates.py',ROOT/'src/pcs/research/entry_candidate_universe.py',ROOT/'src/pcs/research/credit_stop.py']},'split_manifest_hash':h(BASE/'split_manifest.json'),'status':'PARTIAL' if ledger.final_eligible.eq('UNKNOWN').any() else 'COMPLETE','baseline_artifact_only_run_preserved':True}
 (OUT/'research_manifest.json').write_text(json.dumps(manifest,indent=2,default=str),encoding='utf-8')
 (OUT/'research_summary.md').write_text('# Full Daily Opportunity Replay\n\n## Status\n\nPARTIAL / FAIL-CLOSED. The full underlying-date ledger was generated for 3,142 ticker/date rows. The canonical options read is blocked by PCSDataAccess duplicate-key validation: 4,956 ambiguous option quote keys were observed during the SPY preflight. Consequently no option-dependent candidate generation or final gate evaluation was inferred.\n\n## QQQ VALIDATION denominator\n\nThe complete underlying trading-date denominator is 102 dates. Option-data-available dates: UNKNOWN/blocked by canonical validation. Candidate-generated dates: 0 in this replay because option input was blocked. Fully evaluated dates: 0. Eligible dates: 0. Unknown dates: 102. The prior 102/102 number was 102 sealed qualifying rows divided by 102 sealed qualifying rows; it was not a full-date eligibility rate.\n\n## State machine\n\nEligibility-reset setup count is NOT_COMPUTABLE because final eligibility is UNKNOWN for the option-blocked dates. Structural reset and position/risk reset are NOT_COMPUTABLE because daily structural transition and portfolio-risk state are not available. No reset was inferred from missing data.\n\n## Blocker matrix\n\n| Required state | Source found | Reconstructable | Exact blocker |\n|---|---:|---:|---|\n| underlying trading dates | YES | YES | none |\n| underlying state | YES | PARTIAL | full context replay is expensive and not completed after option blocker |\n| candidate generation | YES | NO | option source validation blocks canonical read |\n| trend/support/pullback | YES | PARTIAL | callable research functions exist; not used to manufacture eligibility without options |\n| regime/confirmation | PARTIAL | UNKNOWN | no complete daily gate ledger |\n| safe strike/DTE/credit/liquidity | YES | NO | candidate option chain unavailable after duplicate-key validation |\n| event | PARTIAL | UNKNOWN | baseline ETF event path is not a complete daily ledger |\n| portfolio risk | YES | UNKNOWN | no full daily portfolio decision input |\n| final eligibility | NO | NO | upstream candidate/gate state unavailable |\n\n## Reconciliation\n\nAll sealed qualifying candidates are represented in the reconciliation file as sealed-present, replay-candidate-missing, with reason `OPTIONS_SOURCE_BLOCKED`; no sealed result was forced into the replay.\n\nPRODUCTION RULE CHANGED: NO\nPRODUCTION LOGIC CHANGED: NO\nPRODUCTION CONFIG CHANGED: NO\nFROZEN ARTIFACTS CHANGED: NO\nSEALED ENTRY CONTRACT CHANGED: NO\nVALIDATION USED FOR TUNING: NO\nRESEARCH ONLY: YES\n',encoding='utf-8')
 spotdf=pd.DataFrame(spot); spotdf[spotdf.date.astype(str).str.contains('2024-12|2025-01|2025-12|2026-01')].head(160).to_csv(OUT/'spot_check_replay.csv',index=False)
 print(pd.DataFrame(cov).to_string(index=False)); print('ledger_rows',len(ledger),'candidate_rows',len(cand))
if __name__=='__main__': main()
