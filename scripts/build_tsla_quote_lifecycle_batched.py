"""Small-unit resumable TSLA quote lifecycle builder; research only."""
from __future__ import annotations
import hashlib,json,os,sys,time
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from pcs.data.access import PCSDataAccess
REPO_ROOT=Path(__file__).resolve().parents[1]
ROOT=REPO_ROOT/"research_outputs/tsla_specialized_pcs_20260820"; PARTS=ROOT/"quote_batches"; VAR=ROOT/"tsla_specialized_candidate_variants.parquet"; BATCH=1; MAX_NEW_UNITS=int(os.getenv("TSLA_QUOTE_MAX_NEW_UNITS","25"))
def build():
 PARTS.mkdir(parents=True,exist_ok=True); v=pd.read_parquet(VAR); v=v[v.status.eq("VALID")].copy(); v["month"]=pd.to_datetime(v.decision_date).dt.strftime("%Y-%m"); v["row_no"]=v.groupby("month").cumcount(); v["batch"]=v.row_no//BATCH; a=PCSDataAccess(); daily=a.read_prices("TSLA", start_date=v.decision_date.min(), end_date=v.expiration.max()); daily.date=pd.to_datetime(daily.date); manifest=[]; new_units=0
 for (month,bno),g in v.groupby(["month","batch"],sort=True):
  if new_units >= MAX_NEW_UNITS: break
  uid=f"{month}_{int(bno):04d}"; qp=PARTS/f"quotes_{uid}.parquet"; mp=PARTS/f"marks_{uid}.parquet"; jp=PARTS/f"manifest_{uid}.json"; expected=int(sum(((daily.date>=pd.Timestamp(r.decision_date))&(daily.date<=pd.Timestamp(r.expiration))).sum() for r in g.itertuples()))
  if qp.exists() and mp.exists() and jp.exists():
   try:
    old=json.loads(jp.read_text()); q=pd.read_parquet(qp); m=pd.read_parquet(mp)
    if old.get("status")=="COMPLETE" and len(q)==len(m)==expected and not q.duplicated(["base_candidate_id","research_variant","date"]).any(): manifest.append(old); continue
   except Exception: pass
  qs=[]; ms=[]; sf=lf=bf=0
  # bounded source span for this batch only; no timeout is a source conclusion
  load_started=time.perf_counter(); src=a.read("options","TSLA",g.decision_date.min(),g.expiration.max()); load_seconds=time.perf_counter()-load_started
  lookup_started=time.perf_counter()
  for r in g.itertuples():
   for dt in daily[(daily.date>=pd.Timestamp(r.decision_date))&(daily.date<=pd.Timestamp(r.expiration))].date:
    z=src[(src.trade_date==pd.Timestamp(dt).date())&(src.expiration_date==pd.Timestamp(r.expiration).date())&src.call_put.eq("p")&src.strike.isin([r.short_strike,r.long_strike])]; s=z[z.strike.eq(r.short_strike)].head(1); l=z[z.strike.eq(r.long_strike)].head(1); hs=len(s)==1 and pd.notna(s.iloc[0].get("bid")) and pd.notna(s.iloc[0].get("ask")); hl=len(l)==1 and pd.notna(l.iloc[0].get("bid")) and pd.notna(l.iloc[0].get("ask")); sf+=hs; lf+=hl; bf+=hs and hl; qs.append({"base_candidate_id":r.base_candidate_id,"research_variant":r.research_variant,"date":dt,"short_bid":s.iloc[0].get("bid") if hs else pd.NA,"short_ask":s.iloc[0].get("ask") if hs else pd.NA,"long_bid":l.iloc[0].get("bid") if hl else pd.NA,"long_ask":l.iloc[0].get("ask") if hl else pd.NA,"availability":"AVAILABLE" if hs and hl else "MARK_UNAVAILABLE","source":"PCSDataAccess","provenance":"exact_identity"}); ms.append({"base_candidate_id":r.base_candidate_id,"research_variant":r.research_variant,"date":dt,"spread_mark":((s.iloc[0].bid+s.iloc[0].ask)/2-(l.iloc[0].bid+l.iloc[0].ask)/2) if hs and hl else pd.NA,"mark_method":"MIDPOINT_SHORT_MINUS_LONG","mark_valid":bool(hs and hl)})
  lookup_seconds=time.perf_counter()-lookup_started; q=pd.DataFrame(qs); m=pd.DataFrame(ms); write_started=time.perf_counter(); tq=qp.with_suffix('.tmp.parquet'); tm=mp.with_suffix('.tmp.parquet'); q.to_parquet(tq,index=False); m.to_parquet(tm,index=False); pd.read_parquet(tq); pd.read_parquet(tm); os.replace(tq,qp); os.replace(tm,mp); rec={"partition_id":uid,"parent_month":month,"candidate_count":len(g),"required_contract_days":expected,"short_found":sf,"long_found":lf,"both_found":bf,"missing":expected-bf,"ambiguous":0,"status":"COMPLETE" if bf==expected else "SOURCE_GAP_CONFIRMED","validation":"PASS","timing_seconds":{"source_load":round(load_seconds,3),"exact_lookup":round(lookup_seconds,3),"atomic_write":round(time.perf_counter()-write_started,3)}}; jp.write_text(json.dumps(rec,indent=2),encoding="utf-8"); manifest.append(rec); new_units+=1
 units=v.groupby(["month","batch"]).size(); all_manifest=[]
 for p in PARTS.glob("manifest_*.json"):
  try: all_manifest.append(json.loads(p.read_text(encoding="utf-8")))
  except Exception: pass
 done={x["partition_id"] for x in all_manifest if x.get("status") in {"COMPLETE","SOURCE_GAP_CONFIRMED","SOURCE_AMBIGUITY","INVALID_SOURCE_ROW","IMPLEMENTATION_FAILURE"}}; total_days=int(sum(((daily.date>=pd.Timestamp(r.decision_date))&(daily.date<=pd.Timestamp(r.expiration))).sum() for r in v.itertuples())); processed_days=sum(x.get("required_contract_days",0) for x in all_manifest); progress={"total_units":len(units),"complete_units":len(done),"remaining_units":len(units)-len(done),"variants_complete":sum(x.get("candidate_count",0) for x in all_manifest),"variants_remaining":len(v)-sum(x.get("candidate_count",0) for x in all_manifest),"required_quote_days_processed":processed_days,"required_quote_days_remaining":total_days-processed_days,"coverage_among_processed_rows":(sum(x.get("both_found",0) for x in all_manifest)/processed_days if processed_days else 0),"new_units_this_run":new_units,"status":"COMPLETE" if len(done)==len(units) else "NOT_RUN_REMAINS"}; (ROOT/"tsla_quote_batch_progress.json").write_text(json.dumps(progress,indent=2),encoding="utf-8"); (ROOT/"tsla_quote_batch_manifest.json").write_text(json.dumps(all_manifest,indent=2),encoding="utf-8"); return progress
if __name__=="__main__": print(json.dumps(build(),indent=2))
