from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

BASE=Path('research_outputs/spy_qqq_pcs_baseline_20260821')
OUT=Path('research_outputs/opportunity_episode_analysis_20260821')

def build(ticker):
    sessions=pd.DatetimeIndex(pd.to_datetime(PCSDataAccess().read_prices(ticker)["date"]).dt.normalize().drop_duplicates().sort_values())
    def session_distance(start,end):
        if pd.isna(start): return pd.NA
        return int(((sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))).sum())-1
    c=pd.read_parquet(BASE/f'{ticker}_entry_contract_v2.parquet').copy()
    o=pd.read_parquet(BASE/f'{ticker}_train_validation_outcomes.parquet').copy()
    l=pd.read_parquet(BASE/f'{ticker}_lifecycle_marks.parquet').copy()
    for df,col in [(c,'decision_date'),(o,'decision_date'),(l,'mark_date')]: df[col]=pd.to_datetime(df[col]).dt.normalize()
    entry_ids=set(l.loc[l.is_entry.fillna(False),'candidate_id'])
    exits=(l[l.exit.fillna(False)].sort_values('mark_date').drop_duplicates('candidate_id')[['candidate_id','mark_date']].rename(columns={'mark_date':'exit_date'}))
    x=c.merge(o[['candidate_id','pnl','exit_reason','stop']],on='candidate_id',how='left').merge(exits,on='candidate_id',how='left').sort_values(['decision_date','candidate_id']).reset_index(drop=True)
    x['actual_opened_trade_id']=x.candidate_id.where(x.candidate_id.isin(entry_ids),'')
    cycle=0; end=pd.NaT; cycles=[]
    for _,r in x.iterrows():
        if pd.isna(end) or r.decision_date>end: cycle+=1
        cycles.append(f'{ticker}_BASELINE_CYCLE_{cycle:03d}')
        end=max(end,r.exit_date) if pd.notna(end) else r.exit_date
    x['exposure_cycle_id']=cycles
    out=[]
    for split,lo,hi in [('TRAIN','2020-01-01','2025-12-31'),('VALIDATION','2026-01-01','2026-05-31')]:
        z=x[x.decision_date.between(lo,hi)].copy()
        for gap in (10,15,20):
            ids=[]; start=None; previous=None; eid=0
            for _,r in z.iterrows():
                if start is None or (previous is not None and session_distance(previous,r.decision_date) >= gap): eid+=1; start=r.decision_date
                ids.append(f'{ticker}_{split}_{gap}D_EP_{eid:03d}'); previous=r.decision_date
            q=z.copy(); q['split']=split; q['gap_days']=gap; q['episode_id']=ids; out.append(q)
    x=pd.concat(out,ignore_index=True)
    x['previous_eligible_date']=x.groupby(['ticker','split','gap_days']).decision_date.shift(1)
    x['trading_days_since_previous_eligible']=x.apply(lambda r: session_distance(r.previous_eligible_date,r.decision_date),axis=1)
    x['raw_eligible']=True; x['gate_liquidity_valid']=x.liquidity_valid; x['gate_pit_status_pass']=x.pit_status.eq('PIT_SAFE'); x['gate_event_data_valid']=x.event_data_valid
    for name in ['trend','support','regime','credit','dte','safe_strike','confirmation']: x[f'gate_{name}']='UNKNOWN'
    cols=['decision_date','ticker','split','gap_days','raw_eligible','gate_liquidity_valid','gate_pit_status_pass','gate_event_data_valid','gate_trend','gate_support','gate_regime','gate_credit','gate_dte','gate_safe_strike','gate_confirmation','atr','safe_strike_atr','safe_strike','dte','credit','credit_width_ratio','status','candidate_id','actual_opened_trade_id','exit_date','exposure_cycle_id','episode_id','previous_eligible_date','trading_days_since_previous_eligible','pnl','exit_reason','stop']
    return x[cols]

def main():
    a=pd.concat([build(t) for t in ('SPY','QQQ')],ignore_index=True)
    a.to_csv(OUT/'opportunity_eligibility_component_audit.csv',index=False)
    gates=['raw_eligible','gate_liquidity_valid','gate_pit_status_pass','gate_event_data_valid','gate_trend','gate_support','gate_regime','gate_credit','gate_dte','gate_safe_strike','gate_confirmation']; rows=[]
    for (t,s,y),g in a.assign(calendar_year=a.decision_date.dt.year).groupby(['ticker','split','calendar_year']):
        unique=g.drop_duplicates('candidate_id')
        row={'ticker':t,'split':s,'calendar_year':y,'eligible_rows':len(unique),'actual_opened_trades':int(unique.actual_opened_trade_id.ne('').sum())}
        for col in gates:
            known=unique[col].isin([True,False])
            row[col+'_pass_rate_pct']=round(100*unique.loc[known,col].eq(True).mean(),4) if known.any() else 'UNKNOWN'
        rows.append(row)
    pd.DataFrame(rows).to_csv(OUT/'opportunity_eligibility_gate_pass_rates_by_year.csv',index=False)
    print(pd.DataFrame(rows).to_string(index=False)); print('AUDIT_ROWS_WITH_GAP_REPEATS',len(a),'UNIQUE_ELIGIBLE_ROWS',a.drop_duplicates('candidate_id').shape[0],'UNIQUE_REAL_OPENED',a.drop_duplicates('candidate_id').actual_opened_trade_id.ne('').sum())
if __name__=='__main__': main()
