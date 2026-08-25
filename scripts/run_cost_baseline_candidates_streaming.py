"""Bounded/vectorized COST baseline candidate generation; frozen rules only."""
from pathlib import Path
import json
import time
import pandas as pd
from pcs.research.entry_candidate_universe import build_historical_setup_context, _atr14
from pcs.research.credit_stop import load_quotes_canonical_index
from pcs.data.access import PCSDataAccess

OUT=Path("research_outputs/cost_onboarding_20260821"); OUT.mkdir(parents=True,exist_ok=True)
SAFE=2.3; DTE_LO=30; DTE_HI=45; CREDIT_RATIO=.10
PROGRESS=OUT/"COST_candidate_generation_progress.json"

def write_progress(stage, started, completed, total, dates, total_dates, candidates, durations):
    avg=sum(durations)/len(durations) if durations else None
    payload={"ticker":"COST","stage":stage,"completed_partitions":completed,"total_partitions":total,"completed_decision_dates":dates,"total_decision_dates":total_dates,"candidates_generated_so_far":candidates,"elapsed_seconds":round(time.time()-started,3),"average_seconds_per_completed_partition":round(avg,3) if avg is not None else None,"estimated_remaining_work_units":total-completed if durations else None,"eta_seconds":round((total-completed)*avg,3) if avg is not None else None,"eta_basis":"completed partitions" if avg is not None else None}
    tmp=PROGRESS.with_suffix(".tmp"); tmp.write_text(json.dumps(payload,indent=2),encoding="utf-8"); tmp.replace(PROGRESS)

def daily(symbol):
    d=PCSDataAccess().read_prices(symbol).drop_duplicates("date")
    d.date=pd.to_datetime(d.date).dt.normalize()
    return d.sort_values("date").reset_index(drop=True)

def main():
    stock=daily("COST"); bench=daily("QQQ"); stock["atr14"]=_atr14(stock); rows=[]; reports=[]; started=time.time(); periods=list(pd.period_range("2020Q1","2026Q3",freq="Q")); durations=[]; total_dates=int(stock.date.between("2020-01-02","2026-07-31").sum()); write_progress("candidate_generation",started,0,len(periods),0,total_dates,0,durations)
    for period in periods:
        partition_started=time.time()
        start=max(pd.Timestamp(period.start_time),pd.Timestamp("2020-01-02")); end=min(pd.Timestamp(period.end_time),pd.Timestamp("2026-07-31"))
        if start>end: continue
        idx,meta=load_quotes_canonical_index("COST",start,end); setup=0; qrows=0
        for day in sorted(idx):
            if day<start or day>end: continue
            chain=idx[day]; dr=stock[stock.date.eq(day)]
            if dr.empty or pd.isna(dr.iloc[0].atr14): continue
            qrows+=1; close=float(dr.iloc[0].close); atr=float(dr.iloc[0].atr14)
            ctx=build_historical_setup_context(stock[stock.date<=day],bench[bench.date<=day],day,"COST","QQQ")
            if not ctx.get("available") or ctx.get("entry_context").entry_context_state!="READY": continue
            setup+=1; puts=chain[chain["Call/Put"].astype(str).str.lower().eq("p")].copy(); puts["DTE"]=(puts["Expiry Date"]-day).dt.days; puts=puts[puts.DTE.between(DTE_LO,DTE_HI)]
            for expiry, exp in puts.groupby("Expiry Date"):
                short=exp[(exp.Strike<close)&((close-exp.Strike)/atr>=SAFE)].copy(); long=exp.copy()
                if short.empty: continue
                short=short[(short["Bid Price"]>0)&(short["Ask Price"]>=short["Bid Price"])&(short["Open Interest"]>=500)&(short["Volume"]>=100)]
                long=long[(long["Bid Price"]>0)&(long["Ask Price"]>=long["Bid Price"])]
                if short.empty or long.empty: continue
                a=short.rename(columns={c:f"short_{c}" for c in short.columns}); b=long.rename(columns={c:f"long_{c}" for c in long.columns}); a["_k"]=1; b["_k"]=1
                z=a.merge(b,on="_k").query("long_Strike < short_Strike").copy()
                if z.empty: continue
                z["short_spread"]=(z.short_Ask-z.short_Bid)/((z.short_Ask+z.short_Bid)/2); z["long_spread"]=(z.long_Ask-z.long_Bid)/((z.long_Ask+z.long_Bid)/2); z=z[(z.short_spread<=.18)&(z.long_spread<=.18)]
                z["credit"]=z.short_Bid-z.long_Ask; z["spread_width"]=z.short_Strike-z.long_Strike; z["credit_width_ratio"]=z.credit/z.spread_width; z=z[(z.credit>0)&(z.credit_width_ratio>=CREDIT_RATIO)]
                for r in z.itertuples():
                    rows.append({"date":str(day.date()),"ticker":"COST","underlying_price":close,"atr14":atr,"trend_state":ctx.get("trend_state"),"pullback_state":ctx.get("pullback_state"),"pullback_gate":ctx["pullback_gate_result"].pullback_gate_result,"trend_gate":ctx["trend_gate_result"].trend_gate_result,"reason_codes":ctx.get("reason_codes",[]),"expiration":str(pd.Timestamp(expiry).date()),"dte":int(r.short_DTE),"short_strike":float(r.short_Strike),"long_strike":float(r.long_Strike),"spread_width":float(r.spread_width),"atr_distance":float((close-r.short_Strike)/atr),"short_bid":float(r.short_Bid),"short_ask":float(r.short_Ask),"short_volume":int(r.short_Volume),"short_oi":int(r.short_Open_Interest),"short_delta":float(r.short_Delta) if pd.notna(r.short_Delta) else None,"long_bid":float(r.long_Bid),"long_ask":float(r.long_Ask),"long_volume":int(r.long_Volume),"long_oi":int(r.long_Open_Interest),"credit":float(r.credit),"credit_width_ratio":float(r.credit_width_ratio),"event_state":"NOT_INCLUDED","portfolio_state":"NOT_INCLUDED","timestamp_validation":"UNAVAILABLE"})
        reports.append({"period":str(period),"dates_with_chains":qrows,"setup_pass_dates":setup,"loader":meta}); durations.append(time.time()-partition_started); write_progress("candidate_generation",started,len(reports),len(periods),sum(x["dates_with_chains"] for x in reports),total_dates,len(rows),durations)
    frame=pd.DataFrame(rows)
    if len(frame): frame["candidate_id"]=frame.apply(lambda r:"|".join([r.ticker,r.date,str(r.expiration),format(r.short_strike,'.15g'),format(r.long_strike,'.15g')]),axis=1)
    frame.to_parquet(OUT/"COST_candidate_universe.parquet",index=False)
    report={"ticker":"COST","baseline_rules_unchanged":True,"final_oos_run":False,"candidate_scope":"2020-01-02..2026-07-31","authoritative_options_coverage":"2010-01-04..2026-07-31","candidate_count":len(frame),"unique_entry_dates":int(frame.date.nunique()) if len(frame) else 0,"quarters":reports}
    (OUT/"COST_candidate_generation_report.json").write_text(json.dumps(report,indent=2,default=str),encoding="utf-8"); write_progress("complete",started,len(periods),len(periods),sum(x["dates_with_chains"] for x in reports),total_dates,len(frame),durations); print(json.dumps({"candidate_count":len(frame),"unique_entry_dates":report["unique_entry_dates"]},indent=2))
if __name__=="__main__": main()
