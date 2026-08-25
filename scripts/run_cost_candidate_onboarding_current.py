"""Generic bounded COST candidate runner with resumable quarter batches."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, os, time
import pandas as pd
from pcs.research.entry_candidate_universe import build_historical_setup_context, _atr14
from pcs.research.credit_stop import load_quotes_canonical_index
from pcs.data.access import PCSDataAccess

OUT=Path("research_outputs/cost_onboarding_20260821"); BATCH=OUT/"candidate_batches"; STATE=OUT/"candidate_checkpoint.json"; PROGRESS=OUT/"candidate_progress.json"; BATCH.mkdir(parents=True,exist_ok=True)
SAFE=2.3; DTE_LO=30; DTE_HI=45; CREDIT=.10; WORKERS=int(os.getenv("PCS_CANDIDATE_WORKERS","8")); PERIODS=[str(p) for p in pd.period_range("2020Q1","2026Q3",freq="Q")]
PAIR_COLUMNS=["s_Strike","s_Bid Price","s_Ask Price","s_Delta","s_Open Interest","s_Volume","s_DTE","l_Strike","l_Bid Price","l_Ask Price","l_Delta","l_Open Interest","l_Volume","l_DTE"]
def _pair_schema(frame):
    return frame.reindex(columns=PAIR_COLUMNS)
def atomic_json(path,value):
    tmp=path.with_suffix(path.suffix+f".{os.getpid()}.tmp"); tmp.write_text(json.dumps(value,indent=2,default=str),encoding="utf-8"); tmp.replace(path)
def sha(path):
    h=hashlib.sha256();
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()
def load_daily(sym):
    d=PCSDataAccess().read_prices(sym).drop_duplicates("date"); d.date=pd.to_datetime(d.date).dt.normalize(); d["atr14"]=_atr14(d); return d.sort_values("date").reset_index(drop=True)
def period_work(period,stock,bench):
    p=pd.Period(period); start=max(pd.Timestamp(p.start_time),pd.Timestamp("2020-01-02")); end=min(pd.Timestamp(p.end_time),pd.Timestamp("2026-07-31")); idx,meta=load_quotes_canonical_index("COST",start,end); rows=[]; dates=0; setup=0
    for day in sorted(idx):
        chain=idx[day]; dr=stock[stock.date.eq(day)]
        if dr.empty or pd.isna(dr.iloc[0].atr14): continue
        dates+=1; close=float(dr.iloc[0].close); atr=float(dr.iloc[0].atr14); ctx=build_historical_setup_context(stock[stock.date<=day],bench[bench.date<=day],day,"COST","QQQ")
        if not ctx.get("available") or ctx.get("entry_context").entry_context_state!="READY": continue
        setup+=1; puts=chain[chain["Call/Put"].astype(str).str.lower().eq("p")].copy(); puts["DTE"]=(puts["Expiry Date"]-day).dt.days; puts=puts[puts.DTE.between(DTE_LO,DTE_HI)]
        for expiry,exp in puts.groupby("Expiry Date"):
            s=exp[(exp.Strike<close)&((close-exp.Strike)/atr>=SAFE)&(exp["Bid Price"]>0)&(exp["Ask Price"]>=exp["Bid Price"])&(exp["Open Interest"]>=500)&(exp["Volume"]>=100)].copy(); l=exp[(exp["Bid Price"]>0)&(exp["Ask Price"]>=exp["Bid Price"])].copy()
            if s.empty or l.empty: continue
            a=s.rename(columns={c:f"s_{c}" for c in s.columns}); b=l.rename(columns={c:f"l_{c}" for c in l.columns}); a["k"]=1; b["k"]=1; z=_pair_schema(a.merge(b,on="k")).query("l_Strike < s_Strike");
            if z.empty: continue
            z["ss"]=(z["s_Ask Price"]-z["s_Bid Price"])/((z["s_Ask Price"]+z["s_Bid Price"])/2); z["ls"]=(z["l_Ask Price"]-z["l_Bid Price"])/((z["l_Ask Price"]+z["l_Bid Price"])/2); z=z[(z.ss<=.18)&(z.ls<=.18)]; z["credit"]=z["s_Bid Price"]-z["l_Ask Price"]; z["width"]=z["s_Strike"]-z["l_Strike"]; z["ratio"]=z.credit/z.width; z=z[(z.credit>0)&(z.ratio>=CREDIT)]
            for _,r in z.iterrows():
                cid=hashlib.sha256(f"COST|{day.date()}|{pd.Timestamp(expiry).date()}|{float(r['s_Strike']):.15g}|{float(r['l_Strike']):.15g}".encode()).hexdigest()[:24]
                rows.append({"candidate_id":cid,"ticker":"COST","decision_date":str(day.date()),"expiration":str(pd.Timestamp(expiry).date()),"underlying_price":close,"atr":atr,"safe_strike_atr":SAFE,"dte":int(r["s_DTE"]),"short_strike":float(r["s_Strike"]),"long_strike":float(r["l_Strike"]),"width":float(r["width"]),"credit":float(r["credit"]),"credit_width_ratio":float(r["ratio"]),"short_bid":float(r["s_Bid Price"]),"short_ask":float(r["s_Ask Price"]),"short_delta":float(r["s_Delta"]) if pd.notna(r["s_Delta"]) else None,"short_oi":int(r["s_Open Interest"]),"short_volume":int(r["s_Volume"]),"long_bid":float(r["l_Bid Price"]),"long_ask":float(r["l_Ask Price"]),"long_oi":int(r["l_Open Interest"]),"long_volume":int(r["l_Volume"]),"trend_state":ctx.get("trend_state"),"support_state":ctx.get("support_state"),"predictability_state":ctx.get("predictability_state"),"market_state":"PIT_SAFE_CONTEXT","event_state":"NOT_INCLUDED","pit_status":"PIT_SAFE","source_provenance":"PCSDataAccess:options_v2"})
    frame=pd.DataFrame(rows); path=BATCH/f"period={period}.parquet"; tmp=path.with_suffix(".tmp"); frame.to_parquet(tmp,index=False); os.replace(tmp,path); return {"period":period,"status":"COMMITTED","output_path":str(path),"output_checksum":sha(path),"row_count":len(frame),"decision_dates":dates,"setup_pass_dates":setup,"meta":meta}
def main():
    started=time.time(); stock=load_daily("COST"); bench=load_daily("QQQ"); state=json.loads(STATE.read_text()) if STATE.exists() else {"ticker":"COST","version":"candidate.v2","partitions":{}}
    for p in PERIODS: state["partitions"].setdefault(p,{"status":"PENDING"})
    atomic_json(STATE,state); pending=[p for p in PERIODS if state["partitions"][p].get("status")!="COMMITTED" or not Path(state["partitions"][p].get("output_path","")).exists()]; durations=[]
    def report(stage):
        committed=[p for p in PERIODS if state["partitions"].get(p,{}).get("status")=="COMMITTED"]; failed=[p for p in PERIODS if state["partitions"].get(p,{}).get("status")=="FAILED"]; dates=sum(int(state["partitions"][p].get("decision_dates",0)) for p in committed); candidates=sum(int(state["partitions"][p].get("row_count",0)) for p in committed); avg=sum(durations)/len(durations) if durations else None; atomic_json(PROGRESS,{"ticker":"COST","stage":stage,"workers":WORKERS,"completed_work_units":len(committed),"total_work_units":len(PERIODS),"active_workers":0,"failed_work_units":len(failed),"completed_decision_dates":dates,"candidates_generated":candidates,"elapsed_seconds":round(time.time()-started,2),"average_work_unit_seconds":round(avg,2) if avg else None,"remaining_work_units":len(PERIODS)-len(committed)-len(failed),"eta_seconds":round((len(PERIODS)-len(committed))*avg,2) if avg else None})
    report("resuming")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs={pool.submit(period_work,p,stock,bench):p for p in pending}
        for f in as_completed(futs):
            p=futs[f]; t=time.time()
            try: state["partitions"][p]=f.result(); durations.append(time.time()-t)
            except Exception as e: state["partitions"][p]={"period":p,"status":"FAILED","failure_code":type(e).__name__,"failure_reason":str(e)}
            atomic_json(STATE,state); report("candidate_generation")
    failed=[p for p in PERIODS if state["partitions"][p].get("status")!="COMMITTED"]
    if failed: report("blocked"); raise SystemExit(f"failed work units: {failed}")
    frames=[pd.read_parquet(state["partitions"][p]["output_path"]) for p in PERIODS]; merged=pd.concat(frames,ignore_index=True).sort_values(["decision_date","candidate_id"]).drop_duplicates("candidate_id"); out=OUT/"COST_entry_candidates.parquet"; tmp=out.with_suffix(".tmp"); merged.to_parquet(tmp,index=False); os.replace(tmp,out); atomic_json(STATE,{**state,"final_status":"COMMITTED","merged_output":str(out),"merged_checksum":sha(out),"candidate_count":len(merged)}); report("complete"); print(json.dumps({"candidate_count":len(merged),"output":str(out)},indent=2))
if __name__=="__main__": main()
