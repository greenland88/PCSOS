from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import load_corporate_actions
from pcs.research.variant_b_replay import ReplayPolicy, _replay_lifecycle_batch

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"research_outputs/nvda_price_basis_corrected_authoritative_baseline_20260824"
OUT=ROOT/"research_outputs/nvda_research_agent/round11_train_diagnostic_20260824"
OUT.mkdir(parents=True,exist_ok=True)
c=pd.read_parquet(BASE/"candidates.parquet")
c["date"]=pd.to_datetime(c.date).dt.normalize(); c["expiration"]=pd.to_datetime(c.expiration).dt.normalize()
c=c[c.date.between("2020-01-02","2023-12-31")].copy(); c["candidate_id"]=[f"NVDA_{i}" for i in range(len(c))]
a=PCSDataAccess(); q=a.read_quotes("NVDA","2020-01-02","2024-02-15")
q=q[q.call_put.astype(str).str.lower().eq("p")].copy()
q["trade_date"]=pd.to_datetime(q.trade_date).dt.normalize(); q["expiration_date"]=pd.to_datetime(q.expiration_date).dt.normalize()
q=q.rename(columns={"trade_date":"Trade Date","expiration_date":"Expiry Date","strike":"Strike","bid":"Bid Price","ask":"Ask Price"})
idx={(e,float(s)):g.sort_values("Trade Date") for (e,s),g in q.groupby(["Expiry Date","Strike"],sort=False)}
reg=load_corporate_actions(); policy=ReplayPolicy(); rows=[]
for r in c.to_dict("records"):
    if reg.crossing_action("NVDA",r["date"],r["expiration"]) is not None:
        rows.append({**r,"status":"UNAVAILABLE","exit_reason":"CORPORATE_ACTION_CONTRACT_MAPPING_UNAVAILABLE"}); continue
    result=_replay_lifecycle_batch({"date":r["date"],"expiration":r["expiration"],"short_strike":r["short_strike"],"long_strike":r["long_strike"],"credit":r["credit"]},idx,policy)
    rows.append({**r,**result})
out=pd.DataFrame(rows); out.to_parquet(OUT/"train_lifecycle_outcomes.parquet",index=False)
complete=out[out.status.eq("COMPLETE")].copy(); complete["year"]=complete.date.dt.year
def metrics(g):
    p=g.realized_pnl.astype(float); w=p[p>0]; l=p[p<0]
    return {"trades":len(g),"wins":len(w),"losses":len(l),"stops":int(g.exit_reason.eq("STOP").sum()),"win_rate":float((p>0).mean()) if len(p) else None,"stop_rate":float(g.exit_reason.eq("STOP").mean()) if len(g) else None,"total_pnl":float(p.sum()) if len(p) else None,"expectancy":float(p.mean()) if len(p) else None,"profit_factor":float(w.sum()/abs(l.sum())) if len(l) and l.sum() else None,"avg_win":float(w.mean()) if len(w) else None,"avg_loss":float(l.mean()) if len(l) else None,"worst_trade":float(p.min()) if len(p) else None}
summary=pd.DataFrame([{"group":"ALL",**metrics(complete)}]+[{"group":str(y),**metrics(g)} for y,g in complete.groupby("year")]); summary.to_csv(OUT/"performance_summary.csv",index=False)
complete["pnl_percentile"]=complete.realized_pnl.rank(pct=True,method="average"); complete["bucket"]=pd.cut(complete.pnl_percentile,[-.01,.1,.5,.9,1.01],labels=["WORST_10","MIDDLE_LOWER","MIDDLE_UPPER","BEST_10"]); complete.to_parquet(OUT/"train_outcomes_bucketed.parquet",index=False)
report={"train_candidate_rows":len(c),"complete_trades":len(complete),"unavailable":int((out.status!="COMPLETE").sum()),"unavailable_reason":"CORPORATE_ACTION_CONTRACT_MAPPING_UNAVAILABLE","final_oos_read":False,"production_rules_changed":False,"production_thresholds_changed":False}
(OUT/"diagnostic_manifest.json").write_text(json.dumps(report,indent=2,default=str),encoding="utf-8"); print(summary.to_string(index=False)); print(json.dumps(report,indent=2))
