"""Research-only R0/R1/R2/R3 same-ticker admission overlay."""
from pathlib import Path
import json
import pandas as pd

BASE=Path("research_outputs/spy_qqq_pcs_baseline_20260821"); OUT=Path("research_outputs/same_ticker_stacking_research_20260821"); OUT.mkdir(parents=True,exist_ok=True)

def support_map(ticker):
    p=Path(f"research_outputs/safe_strike_risk_map_v0_1/trend_histories/{ticker}_trend.parquet")
    if not p.exists(): return {}
    d=pd.read_parquet(p); out={}
    for _,r in d.iterrows():
        try: out[pd.Timestamp(r.date).date()]=json.loads(r.support).get("support_confluence_state")
        except Exception: out[pd.Timestamp(r.date).date()]=None
    return out

def load(ticker):
    c=pd.read_parquet(BASE/f"{ticker}_entry_contract_v2.parquet"); o=pd.read_parquet(BASE/f"{ticker}_train_validation_outcomes.parquet"); l=pd.read_parquet(BASE/f"{ticker}_lifecycle_marks.parquet")
    c["decision_date"]=pd.to_datetime(c.decision_date); o["decision_date"]=pd.to_datetime(o.decision_date); l["mark_date"]=pd.to_datetime(l.mark_date)
    exits=l[l.exit.fillna(False)].sort_values("mark_date").drop_duplicates("candidate_id")[['candidate_id','mark_date']].rename(columns={'mark_date':'exit_date'})
    x=c.merge(o,on='candidate_id',suffixes=('','_outcome')).merge(exits,on='candidate_id',how='left').sort_values(['decision_date','candidate_id']).reset_index(drop=True)
    smap=support_map(ticker); x['support_state']=[smap.get(d.date()) for d in x.decision_date]
    x['split']=x.decision_date.map(lambda d:'TRAIN' if d<=pd.Timestamp('2025-12-31') else 'VALIDATION' if d<=pd.Timestamp('2026-05-31') else 'FINAL_OOS')
    return x

def stats(x):
    p=x.pnl.astype(float); w=p[p>0]; l=p[p<0]; curve=p.cumsum(); dd=curve-curve.cummax()
    return {'baseline_qualifying_candidates':int(len(x)), 'opened_trades':int(len(x)), 'total_pnl':float(p.sum()) if len(p) else 0.0,'expectancy':float(p.mean()) if len(p) else None,'profit_factor':float(w.sum()/abs(l.sum())) if len(l) else None,'win_rate':float((p>0).mean()) if len(p) else None,'stop_rate':float(x.stop.mean()) if len(x) else None,'average_winner':float(w.mean()) if len(w) else None,'average_loser':float(l.mean()) if len(l) else None,'worst_trade':float(p.min()) if len(p) else None,'max_drawdown':float(dd.min()) if len(p) else None,'tail_loss_count':int((p<=-200).sum()),'maximum_simultaneous_positions':int(x.open_count.max()) if len(x) else 0}

def apply_variants(x):
    open_count=0; active=[]; rows=[]
    for _,r in x.iterrows():
        day=r.decision_date
        active=[e for e in active if pd.notna(e) and e>=day]
        open_count=len(active)
        weak=str(r.support_state).upper() in {'WEAK','NO_VALID_SUPPORT','UNKNOWN','NONE','NAN'} or pd.isna(r.support_state)
        row=r.to_dict(); row['open_count']=open_count; row['r0_admit']=True; row['r1_admit']=not weak; row['r2_admit']=open_count==0; row['r3_admit']=(not weak) and open_count==0
        rows.append(row)
        if pd.notna(r.exit_date): active.append(r.exit_date)
    return pd.DataFrame(rows)

def variant_report(x, flag):
    admitted=x[x[flag]].copy(); rejected=x[~x[flag]].copy(); s=stats(admitted); s.update({'rejected_due_to_support':int((~x.r1_admit).sum()) if flag in {'r1_admit','r3_admit'} else 0,'rejected_due_to_same_ticker_stacking':int((~x.r2_admit).sum()) if flag in {'r2_admit','r3_admit'} else 0,'trade_reduction_pct':float((1-len(admitted)/len(x))*100) if len(x) else 0,'rejected_winners':int((rejected.pnl>0).sum()),'rejected_losers':int((rejected.pnl<0).sum()),'pnl_of_rejected_trades':float(rejected.pnl.sum()) if len(rejected) else 0.0}); return s

def clusters(x):
    x=x.sort_values(['decision_date','candidate_id']).reset_index(drop=True); rows=[]; cur=[]; max_exit=None
    for _,r in x.iterrows():
        if cur and r.decision_date > max_exit:
            rows.append(cur); cur=[]; max_exit=None
        cur.append(r); max_exit=max(max_exit,r.exit_date) if max_exit is not None and pd.notna(r.exit_date) else r.exit_date
    if cur: rows.append(cur)
    out=[]
    for c in rows:
        z=pd.DataFrame(c)
        if len(z)<2: continue
        additional=z.iloc[1:]
        out.append({'cluster_start':str(z.decision_date.min().date()),'cluster_end':str(z.exit_date.max().date()),'baseline_entries':len(z),'maximum_simultaneous_positions':int(z.open_count.max()+1),'baseline_cluster_pnl':float(z.pnl.sum()),'r2_cluster_pnl':float(z.iloc[:1].pnl.sum()),'prevented_loss':float(additional.pnl.sum()),'sacrificed_profit':float(additional.loc[additional.pnl>0,'pnl'].sum())})
    return pd.DataFrame(out)

def main():
    report={'module':'same_ticker_stacking_research','version':'20260821.v1','final_oos_used_for_rule_selection':False,'production_rules_changed':False,'cross_ticker_bucket_tested':False,'tickers':{}}
    for t in ('SPY','QQQ'):
        x=apply_variants(load(t)); x.to_parquet(OUT/f'{t}_overlay_rows.parquet',index=False)
        rows=[]
        for v,f in [('R0_BASELINE','r0_admit'),('R1_NO_WEAK_SUPPORT','r1_admit'),('R2_NO_SAME_TICKER_STACKING','r2_admit'),('R3_COMBINED','r3_admit')]:
            for split in ('TRAIN','VALIDATION','FINAL_OOS'):
                s=variant_report(x[x.split==split],f); s.update({'variant':v,'split':split}); rows.append(s)
        pd.DataFrame(rows).to_csv(OUT/f'{t}_variant_metrics.csv',index=False)
        cl=clusters(x[x.split.isin(['TRAIN','VALIDATION','FINAL_OOS'])]); cl.insert(0,'ticker',t); cl.to_csv(OUT/f'{t}_stacking_clusters.csv',index=False)
        june=x[x.decision_date.between('2026-06-01','2026-06-04')]
        report['tickers'][t]={'variant_metrics':rows,'stacking_clusters':cl.to_dict('records'),'additional_stacked_trades':int(sum(max(0,r['baseline_entries']-1) for r in cl.to_dict('records'))),'additional_stacked_trades_pnl':float(sum(r['prevented_loss'] for r in cl.to_dict('records'))),'june_cluster_baseline_pnl':float(june.pnl.sum()),'june_cluster_r2_pnl':float(june.loc[june.r2_admit,'pnl'].sum()),'june_cluster_prevented_loss':float(june.loc[~june.r2_admit,'pnl'].sum())}
    (OUT/'research_report.json').write_text(json.dumps(report,indent=2,default=str)); print(json.dumps(report,indent=2,default=str))

if __name__=='__main__': main()
