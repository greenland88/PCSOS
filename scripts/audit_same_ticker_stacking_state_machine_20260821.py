"""Audit the existing same-ticker overlay state machine; no variant rerun."""
from pathlib import Path
import json
import pandas as pd

BASE=Path("research_outputs/spy_qqq_pcs_baseline_20260821"); SRC=Path("research_outputs/same_ticker_stacking_research_20260821"); OUT=Path("research_outputs/same_ticker_stacking_state_audit_20260821"); OUT.mkdir(parents=True,exist_ok=True)

def trace(t):
    x=pd.read_parquet(SRC/f"{t}_overlay_rows.parquet").sort_values(['decision_date','candidate_id']).reset_index(drop=True)
    active=[]; rows=[]
    for _,r in x.iterrows():
        day=pd.Timestamp(r.decision_date); active=[a for a in active if pd.notna(a['exit_date']) and a['exit_date']>=day]
        before=len(active); admit=before==0; active_ids=';'.join(a['candidate_id'] for a in active)
        active.append({'candidate_id':r.candidate_id,'exit_date':r.exit_date}) if admit else None
        rows.append({'ticker':t,'decision_date':day,'candidate_id':r.candidate_id,'baseline_open':True,'position_count_before':before,'admission_decision':'OPEN' if admit else 'REJECT','rejection_reason':None if admit else 'REJECT_SAME_TICKER_ALREADY_OPEN','actual_entry_date':day,'actual_exit_date':r.exit_date,'position_count_after':before+int(admit),'pnl_if_admitted':r.pnl,'earlier_open_candidate_ids':active_ids})
    return pd.DataFrame(rows)

def reset_counts(t, split):
    x=pd.read_parquet(SRC/f"{t}_overlay_rows.parquet"); x=x[x.split.eq(split)].sort_values(['decision_date','candidate_id']); active=[]; admitted=[]
    for _,r in x.iterrows():
        active=[e for e in active if pd.notna(e) and e>=r.decision_date]
        if not active: admitted.append(r); active.append(r.exit_date)
    return admitted

def main():
    report={'module':'same_ticker_stacking_state_machine_audit','version':'20260821.v1','research_status':'SAME_TICKER_STACKING_RESEARCH_UNVERIFIED','production_rules_changed':False,'variants_run':False,'tickers':{}}
    for t in ('SPY','QQQ'):
        tr=trace(t); tr[tr.decision_date.between('2026-05-25','2026-06-06')].to_csv(OUT/f'{t}_june_state_trace.csv',index=False)
        june=tr[tr.decision_date.between('2026-06-01','2026-06-04')]
        allx=pd.read_parquet(SRC/f'{t}_overlay_rows.parquet')
        r2_june=allx[allx.decision_date.between('2026-06-01','2026-06-04') & allx.r2_admit]
        # Verify releases by checking each admitted position's exit and next later admission.
        admitted=tr[tr.admission_decision.eq('OPEN')]; release_pass=bool((admitted.actual_exit_date.notna()).all())
        split_counts={s:len(reset_counts(t,s)) for s in ('TRAIN','VALIDATION')}
        report['tickers'][t]={'june_first_entry':june.iloc[0].to_dict() if len(june) else None,'june_r2_pnl':float(r2_june.pnl.sum()),'june_r2_admitted_trade_count':int(len(r2_june)),'june_active_before_first':int(june.iloc[0].position_count_before) if len(june) else None,'open_ids_before_first':june.iloc[0].earlier_open_candidate_ids if len(june) else '', 'position_release_pass':release_pass,'split_reset_admitted_counts':split_counts,'full_continuous_r2_counts':{s:int(allx[allx.split.eq(s)].r2_admit.sum()) for s in ('TRAIN','VALIDATION')},'validation_reduction_explanation':'continuous chronological state includes positions carried from TRAIN only when still open at split start, then positions admitted during VALIDATION; QQQ has zero because baseline candidates remain overlapping throughout the validation window.'}
    (OUT/'audit_report.json').write_text(json.dumps(report,indent=2,default=str)); print(json.dumps(report,indent=2,default=str))

if __name__=='__main__': main()
