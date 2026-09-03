"""Read-only comparison of the former legacy-backed input and pinned snapshot."""
from pathlib import Path
import hashlib,json
import pandas as pd
from pcs.trend.opportunity_engine import replay_opportunities

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/"data/parquet/daily/symbol=NVDA"; OUT=ROOT/"research_outputs/nvda_pcs_2026_opportunity_engine"
def desc(p):
 f=pd.read_parquet(p); f["date"]=pd.to_datetime(f.date).dt.normalize(); return {"file":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"rows":len(f),"min_date":str(f.date.min().date()),"max_date":str(f.date.max().date()),"columns":list(f.columns),"frame":f}
def main():
 old_parts=[desc(DATA/"year=2024/NVDA_2024.parquet"),desc(DATA/"year=2025/NVDA_2025.parquet"),desc(DATA/"year=2026/NVDA_2026.parquet")]
 new_path=DATA/"year=2026/generations/4382d8f1e9e96ba3df054072.parquet"; new=desc(new_path)
 old=pd.concat([x["frame"] for x in old_parts],ignore_index=True).sort_values("date").drop_duplicates("date").reset_index(drop=True)
 newf=new["frame"].sort_values("date").drop_duplicates("date").reset_index(drop=True)
 old["symbol"]="NVDA"; newf["symbol"]="NVDA"
 keys=["symbol","date"]; cols=["open","high","low","close","volume"]
 a=old.set_index(keys); b=newf.set_index(keys); joined=a.join(b,lsuffix="_old",rsuffix="_new",how="outer")
 diffs=[]
 for c in cols:
  mask=joined[f"{c}_old"].fillna(float("nan")).ne(joined[f"{c}_new"].fillna(float("nan")))
  if mask.any():
   for ix in joined.index[mask]: diffs.append({"symbol":ix[0],"date":ix[1].date().isoformat(),"field":c,"old":joined.loc[ix,f"{c}_old"],"new":joined.loc[ix,f"{c}_new"],"delta":joined.loc[ix,f"{c}_new"]-joined.loc[ix,f"{c}_old"]})
 old_run=replay_opportunities("NVDA",old,"2026-01-01","2026-09-01",minimum_warmup_rows=200)
 new_run=replay_opportunities("NVDA",newf,"2026-01-01","2026-09-01",minimum_warmup_rows=200)
 feature_cols=["sma20","sma50","ema200","atr14","rsi14","adx14","macd_histogram","macd_histogram_change","rvol20","primary_support","support_type","distance_to_support_atr","structural_trend","short_term_phase","opportunity_path","timing_action","reason_codes","diagnostic_flags"]
 od=old_run.set_index("date"); nd=new_run.set_index("date"); rows=[]
 for date in sorted(set(od.index)&set(nd.index)):
  change=any(str(od.loc[date,c])!=str(nd.loc[date,c]) for c in feature_cols if c in od and c in nd)
  if change:
   x={"date":date,"old_action":od.loc[date,"timing_action"],"new_action":nd.loc[date,"timing_action"]}
   for c in feature_cols:
    if c in od and c in nd and str(od.loc[date,c])!=str(nd.loc[date,c]): x[f"old_{c}"]=od.loc[date,c]; x[f"new_{c}"]=nd.loc[date,c]
   rows.append(x)
 Path(OUT/"nvda_input_decision_diff.csv").write_text(pd.DataFrame(rows).to_csv(index=False),encoding="utf-8")
 def summary(r): return {"rows":len(r),"actions":r.timing_action.value_counts().to_dict(),"min_date":r.date.min(),"max_date":r.date.max()}
 report={"OLD_INPUT":{"parts":[{k:v for k,v in x.items() if k!="frame"} for x in old_parts],"rows":len(old),"min_date":str(old.date.min().date()),"max_date":str(old.date.max().date()),"price_basis":"canonical_adjusted (manifest/source metadata unavailable for legacy files)","corporate_action_version":"canonical_identity (not embedded in legacy parquet)","reproducible":True},"NEW_INPUT":{"file":{k:v for k,v in new.items() if k!="frame"},"rows":len(newf),"min_date":str(newf.date.min().date()),"max_date":str(newf.date.max().date()),"generation_id":"4382d8f1e9e96ba3df054072","fingerprint":"36518f20c2aadb53844dc769b6b26bac0a7b273b78fbb993f52be8cf3b48dba0"},"row_diff":{"only_old":sorted(set(a.index)-set(b.index)),"only_new":sorted(set(b.index)-set(a.index)),"field_diff_count":len(diffs),"field_max_abs_delta":{c:max([abs(float(x["delta"])) for x in diffs if x["field"]==c] or [0]) for c in cols},"first_20":diffs[:20]},"old_replay":summary(old_run),"new_replay":summary(new_run),"feature_first_difference":rows[0]["date"] if rows else None,"decision_diff_rows":len(rows),"action_transition_summary":pd.crosstab(pd.DataFrame(rows).old_action,pd.DataFrame(rows).new_action).to_dict() if rows else {},"old_ready_on_new":[]}
 old_ready=old_run[old_run.timing_action=="TIMING_ENTRY_READY"]
 for _,r in old_ready.iterrows():
  n=new_run[new_run.date==r.date].iloc[0]; report["old_ready_on_new"].append({"date":r.date,"old_action":r.timing_action,"new_action":n.timing_action,"new_path":n.opportunity_path,"new_reason":n.reason_codes})
 (OUT/"nvda_input_decision_diff.json").write_text(json.dumps(report,indent=2,default=str),encoding="utf-8")
 print(json.dumps(report,default=str))
if __name__=="__main__": main()
