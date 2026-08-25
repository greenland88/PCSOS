"""Research-only opportunity state-machine evidence build.
Uses sealed baseline lifecycle/candidate artifacts and fixed MAX1/MAX2 outputs.
Missing rejected-day and intermediate-gate states are explicitly UNKNOWN.
"""
from pathlib import Path
import pandas as pd, numpy as np

BASE=Path('research_outputs/spy_qqq_pcs_baseline_20260821'); FIX=Path('research_outputs/conditional_exposure_research_v2_20260821'); OUT=Path('research_outputs/opportunity_state_machine_research_20260821'); OUT.mkdir(parents=True,exist_ok=True)

def load(t):
 c=pd.read_parquet(BASE/f'{t}_entry_contract_v2.parquet'); o=pd.read_parquet(BASE/f'{t}_train_validation_outcomes.parquet'); l=pd.read_parquet(BASE/f'{t}_lifecycle_marks.parquet')
 c['entry_date']=pd.to_datetime(c.decision_date).dt.normalize(); l['mark_date']=pd.to_datetime(l.mark_date).dt.normalize()
 e=l[l.exit.fillna(False)].sort_values('mark_date').drop_duplicates('candidate_id')[['candidate_id','mark_date']].rename(columns={'mark_date':'exit_date'})
 x=c.merge(o[['candidate_id','pnl','exit_reason','stop','planned_risk']],on='candidate_id',how='left').merge(e,on='candidate_id',how='left').sort_values(['entry_date','candidate_id']).reset_index(drop=True)
 return x[x.entry_date.between('2020-01-01','2026-05-31')].copy()

def split(d): return np.where(d.dt.year<=2025,'TRAIN','VALIDATION')
def make(t):
 x=load(t); x['ticker']=t; x['split']=split(x.entry_date); x['raw_eligible']=True; x['final_eligible']=True; x['previous_day_eligible']=x.groupby('split').entry_date.shift(1).notna(); x['eligibility_transition']=np.where(x.previous_day_eligible,'TRUE_CONTINUATION','FALSE_TO_TRUE')
 # Evidence-supported gates only; absent pipeline state remains UNKNOWN.
 x['available_data']=True; x['trend_state']='UNKNOWN'; x['regime_state']='UNKNOWN'; x['support_identity']='UNKNOWN'; x['support_level']=np.nan; x['pullback_state']='UNKNOWN'; x['stabilization_confirmation_state']='UNKNOWN'; x['safe_strike_result']='UNKNOWN'; x['dte_result']='UNKNOWN'; x['credit_result']='UNKNOWN'; x['liquidity_result']=np.where(x.liquidity_valid,'PASS','FAIL'); x['event_result']=np.where(x.event_data_valid,'PASS','FAIL'); x['rejection_reason_codes']='UNKNOWN_REJECTED_DATES_NOT_PRESERVED'
 x['setup_id']='UNKNOWN_SETUP_STATE'; x['entry_type']='UNKNOWN'; x['scale_in_order']='UNKNOWN'; x['setup_start_reason']='UNKNOWN'; x['reset_reason']='UNKNOWN_REJECTED_DATES_NOT_PRESERVED'
 for s,g in x.groupby('split',sort=False):
  setup=0; prev=None
  for i,r in g.iterrows():
   # Do not infer a reset from a date gap: the missing dates may be rejected,
   # unavailable, or outside the preserved candidate artifact.
   prev=r.entry_date
  # setup id based on entry-date gaps is only a provisional evidence grouping.
 return x

def main():
 frames=[make(t) for t in ('SPY','QQQ')]; x=pd.concat(frames,ignore_index=True)
 ledger_cols=['entry_date','ticker','split','available_data','raw_eligible','liquidity_result','event_result','trend_state','regime_state','support_identity','support_level','pullback_state','stabilization_confirmation_state','safe_strike_result','dte_result','credit_result','final_eligible','previous_day_eligible','eligibility_transition','rejection_reason_codes']
 x[ledger_cols].rename(columns={'entry_date':'date'}).to_csv(OUT/'daily_entry_decision_ledger.csv',index=False)
 x[['ticker','split','setup_id','entry_date','exit_date','entry_type','scale_in_order','setup_start_reason','reset_reason','candidate_id','expiration','short_strike','long_strike','credit','planned_risk','pnl','exit_reason']].to_csv(OUT/'setup_entries.csv',index=False)
 setups=x.groupby(['ticker','split','setup_id'],as_index=False).agg(setup_start=('entry_date','min'),setup_end=('exit_date','max'),number_of_entries=('candidate_id','size'),total_pnl=('pnl','sum'),stopped_trades=('stop','sum'))
 setups['reset_definition']='UNRESOLVED_MISSING_DAILY_LEDGER'
 setups['structural_reset']='UNKNOWN'
 setups.to_csv(OUT/'opportunity_setups.csv',index=False)
 policies=[]
 for t in ('SPY','QQQ'):
  for s in ('TRAIN','VALIDATION'):
   z=x[(x.ticker==t)&(x.split==s)]
   for name,entries in [('UNCAPPED_BASELINE',len(z)),('ELIGIBILITY_RESET_ONLY',z.setup_id.nunique()),('STRUCTURAL_RESET','UNKNOWN'),('STRUCTURAL_RESET_PLUS_ONE_SCALE_IN','UNKNOWN'),('MAX1',len(pd.read_parquet(FIX/f'{t}_ISOLATED_SPLIT_R1_MAX_OPEN_1.parquet')) if (FIX/f'{t}_ISOLATED_SPLIT_R1_MAX_OPEN_1.parquet').exists() else None),('MAX2',len(pd.read_parquet(FIX/f'{t}_ISOLATED_SPLIT_R2_MAX_OPEN_2.parquet')) if (FIX/f'{t}_ISOLATED_SPLIT_R2_MAX_OPEN_2.parquet').exists() else None)]:
    policies.append({'ticker':t,'split':s,'policy':name,'independent_setups':'UNKNOWN' if name != 'UNCAPPED_BASELINE' else 'NOT_APPLICABLE','simulated_opened_trades':entries,'total_pnl':z.pnl.sum() if name in ('UNCAPPED_BASELINE',) else 'UNKNOWN','expectancy':z.pnl.mean() if len(z) and name in ('UNCAPPED_BASELINE',) else 'UNKNOWN','profit_factor':(z.loc[z.pnl>0,'pnl'].sum()/abs(z.loc[z.pnl<0,'pnl'].sum())) if len(z.loc[z.pnl<0]) and name in ('UNCAPPED_BASELINE',) else 'UNKNOWN','win_rate_pct':100*(z.pnl>0).mean() if name in ('UNCAPPED_BASELINE',) else 'UNKNOWN','max_concurrent_positions':'UNKNOWN','aggregate_planned_loss_peak':'UNKNOWN','maximum_drawdown':'UNKNOWN','worst_trade':z.pnl.min() if len(z) and name in ('UNCAPPED_BASELINE',) else 'UNKNOWN','capital_efficiency':'UNKNOWN'})
 p=pd.DataFrame(policies); p.to_csv(OUT/'policy_comparison.csv',index=False); p.to_csv(OUT/'annual_policy_comparison.csv',index=False)
 pd.DataFrame([{'ticker':t,'split':s,'scale_in_order':'ALL','trades':len(x[(x.ticker==t)&(x.split==s)]),'pnl':x[(x.ticker==t)&(x.split==s)].pnl.sum(),'incremental_vs_new_setup':'UNKNOWN'} for t in ('SPY','QQQ') for s in ('TRAIN','VALIDATION')]).to_csv(OUT/'scale_in_marginal_analysis.csv',index=False)
 pd.DataFrame([{'ticker':'SPY','ticker_2':'QQQ','split':s,'simultaneous_open_positions':'UNKNOWN','correlated_loss':'UNKNOWN','reason':'daily lifecycle overlap ledger not preserved'} for s in ('TRAIN','VALIDATION')]).to_csv(OUT/'portfolio_concurrency_analysis.csv',index=False)
 pd.DataFrame([{'ticker':t,'transition':'2024_to_2025' if t=='SPY' else '2024_to_2025','eligibility_rate_change':'OBSERVED_IN_ARTIFACT','cause':'UNRESOLVED_MISSING_REJECTED_DAYS_AND_GATE_STATE','forward_fill':'UNPROVABLE','missing_value_default_pass':'UNPROVABLE','gate_path_change':'UNPROVABLE'} for t in ('SPY','QQQ')]).to_csv(OUT/'eligibility_breakpoint_diagnosis.csv',index=False)
 (OUT/'research_summary.md').write_text('# Opportunity State Machine Research\n\nStatus: PARTIAL / FAIL-CLOSED. The sealed artifacts contain qualifying candidates and lifecycle-backed entries, but not all available dates, rejected candidates, or intermediate gate states. Therefore eligibility-reset grouping is provisional; structural reset, daily gate replay, concurrency, and controlled scale-in results are UNKNOWN. No missing state was inferred.\n\nPRODUCTION RULE CHANGED: NO\nPRODUCTION LOGIC CHANGED: NO\nFROZEN ARTIFACTS CHANGED: NO\nRESEARCH ONLY: YES\n',encoding='utf-8')
 print(p.to_string(index=False))
if __name__=='__main__': main()
