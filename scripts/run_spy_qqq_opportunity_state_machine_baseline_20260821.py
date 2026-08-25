"""Research-only policy comparison from the verified run_backtest baseline."""
from pathlib import Path
import json, hashlib
import pandas as pd
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"research_outputs/spy_qqq_pcs_baseline_rebuilt_20260821"
OUT=ROOT/"research_outputs/opportunity_state_machine_research_baseline_v2_20260821"
OUT.mkdir(parents=True,exist_ok=True)
TRAIN=("2020-02-28","2025-12-31"); VALID=("2026-01-01","2026-05-31")

coverage=pd.DataFrame([
 {"component":"trend context","status":"ENFORCED","evidence":"credit_stop.precompute_trend_lookup"},
 {"component":"DTE","status":"ENFORCED","evidence":"select_expiration DTE 20..45"},
 {"component":"option type","status":"ENFORCED","evidence":"select_pair Call/Put == p"},
 {"component":"safe strike","status":"ENFORCED","evidence":"close - 2.3 * ATR nearest available short"},
 {"component":"spread width","status":"ENFORCED","evidence":"long strike exactly short - 5"},
 {"component":"entry quote validity","status":"ENFORCED","evidence":"positive ordered bid/ask"},
 {"component":"credit","status":"ENFORCED","evidence":"positive conservative credit; >=15% width"},
 {"component":"formal liquidity gate","status":"NOT_IN_PATH","evidence":"no OI/volume/spread scoring gate"},
 {"component":"event gate","status":"NOT_IN_PATH","evidence":"company earnings marked not applicable"},
 {"component":"portfolio gate","status":"NOT_IN_PATH","evidence":"no portfolio state in run_backtest"},
 {"component":"DecisionEngine","status":"NOT_IN_PATH","evidence":"baseline creates qualifying contracts directly"},
])
coverage.to_csv(OUT/"entry_v1_baseline_coverage.csv",index=False)

def daily_dates(t):
 d=pd.concat([pd.read_parquet(p) for p in sorted((ROOT/"data/parquet/daily"/f"symbol={t}").rglob("*.parquet"))])
 return pd.to_datetime(d["date"]).dt.normalize().drop_duplicates().sort_values().tolist()
def split(d):
 return "TRAIN" if pd.Timestamp(TRAIN[0])<=d<=pd.Timestamp(TRAIN[1]) else "VALIDATION" if pd.Timestamp(VALID[0])<=d<=pd.Timestamp(VALID[1]) else None

allcontracts=[]; ledger=[]; setups=[]; entries=[]
for t in ("SPY","QQQ"):
 c=pd.read_parquet(SRC/f"{t}_entry_contract_v2.parquet")
 l=pd.read_parquet(SRC/f"{t}_lifecycle_marks.parquet")
 c["decision_date"]=pd.to_datetime(c.decision_date).dt.normalize(); l["mark_date"]=pd.to_datetime(l.mark_date).dt.normalize()
 c["split"]=c.decision_date.map(split); c=c[c["split"].notna()].copy()
 exits=l[l.exit].sort_values("mark_date").groupby("candidate_id",as_index=False).first()[["candidate_id","mark_date","pnl","stop_triggered"]].rename(columns={"mark_date":"exit_date","pnl":"realized_pnl"})
 c=c.merge(exits,on="candidate_id",how="left")
 c["exit_date"]=c.exit_date.fillna(c.expiration); c["realized_pnl"]=c.realized_pnl.fillna(0.0); c["stop_triggered"]=c.stop_triggered.fillna(False)
 for sp in ("TRAIN","VALIDATION"):
  x=c[c.split.eq(sp)].sort_values(["decision_date","candidate_id"]).copy()
  calendar=[d for d in daily_dates(t) if split(d)==sp]
  prev=None; sid=0
  ids={}
  for i,r in x.iterrows():
   pos=calendar.index(r.decision_date)
   if prev is None or pos!=prev+1: sid+=1
   ids[i]=f"{t}_{sp}_SETUP_{sid:04d}"; prev=pos
  x["setup_id"]=x.index.map(ids); x["entry_order_in_setup"]=x.groupby("setup_id").cumcount()+1
  allcontracts.append(x)
  for setup_id,g in x.groupby("setup_id"):
   setups.append({"ticker":t,"split":sp,"setup_id":setup_id,"setup_start":g.decision_date.min(),"setup_end":g.decision_date.max(),"qualifying_entries":len(g),"setup_definition":"consecutive qualifying trading dates; False/absent candidate resets"})
  bydate=x.groupby("decision_date").size().to_dict()
  sid_bydate=x.groupby("decision_date").setup_id.first().to_dict()
  for d in calendar:
   ledger.append({"date":d,"ticker":t,"split":sp,"baseline_qualifying_candidate_count":bydate.get(d,0),"final_eligible":bydate.get(d,0)>0,"setup_id":sid_bydate.get(d),"status":"QUALIFYING" if bydate.get(d,0) else "NO_QUALIFYING_BASELINE_CANDIDATE"})
allc=pd.concat(allcontracts,ignore_index=True)
pd.DataFrame(ledger).to_csv(OUT/"daily_decision_ledger.csv",index=False)
pd.DataFrame(setups).to_csv(OUT/"opportunity_setups.csv",index=False)

def apply_policy(x,name):
 x=x.sort_values(["decision_date","candidate_id"]).copy()
 if name=="UNCAPPED_BASELINE": return x.assign(policy_entry=True,entry_type="BASELINE")
 if name=="SETUP_ONE": return x.assign(policy_entry=x.entry_order_in_setup.eq(1),entry_type=np.where(x.entry_order_in_setup.eq(1),"NEW_SETUP","REJECT_SETUP_LIMIT"))
 if name=="SETUP_ONE_PLUS_SCALE": return x.assign(policy_entry=x.entry_order_in_setup.le(2),entry_type=np.where(x.entry_order_in_setup.eq(1),"NEW_SETUP",np.where(x.entry_order_in_setup.eq(2),"SCALE_IN","REJECT_SETUP_LIMIT")))
 cap=1 if name=="MAX1" else 2; admitted=[]; active=[]
 for i,r in x.iterrows():
  active=[a for a in active if a["exit_date"]>=r.decision_date]
  ok=len(active)<cap
  if ok: active.append({"exit_date":r.exit_date})
  admitted.append(ok)
 return x.assign(policy_entry=admitted,entry_type=np.where(admitted,"OPEN","REJECT_MAX_OPEN"))

def metrics(x):
 y=x[x.policy_entry].copy()
 if y.empty: return {"entries":0,"total_pnl":0.0,"expectancy":None,"profit_factor":None,"max_drawdown":None,"peak_aggregate_planned_loss":0.0,"return_on_peak_planned_loss":None}
 y=y.sort_values(["exit_date","candidate_id"]); daily=y.groupby("exit_date").realized_pnl.sum().sort_index(); eq=daily.cumsum(); dd=(eq-eq.cummax()).min()
 wins=y.realized_pnl[y.realized_pnl>0].sum(); loss=-y.realized_pnl[y.realized_pnl<0].sum()
 points=sorted(set(y.decision_date).union(set(y.exit_date))); peak=0.0
 for d in points: peak=max(peak,float(y[(y.decision_date<=d)&(y.exit_date>=d)].planned_loss.sum()))
 return {"entries":len(y),"total_pnl":float(y.realized_pnl.sum()),"expectancy":float(y.realized_pnl.mean()),"profit_factor":float(wins/loss) if loss else None,"max_drawdown":float(dd),"peak_aggregate_planned_loss":peak,"return_on_peak_planned_loss":float(y.realized_pnl.sum()/peak) if peak else None}

out=[]; policy_rows=[]
for (t,sp),x in allc.groupby(["ticker","split"]):
 for p in ("UNCAPPED_BASELINE","SETUP_ONE","SETUP_ONE_PLUS_SCALE","MAX1","MAX2"):
  q=apply_policy(x,p); policy_rows.append(q); out.append({"ticker":t,"split":sp,"policy":p,**metrics(q)})
pol=pd.concat(policy_rows,ignore_index=True); pol.to_parquet(OUT/"policy_entries.parquet",index=False)
pd.DataFrame(out).to_csv(OUT/"policy_comparison.csv",index=False)
summary={"source":"build_spy_qqq_pcs_artifacts.py -> credit_stop.run_backtest","final_oos_read":False,"setup_rule":"consecutive qualifying trading dates only","policies":["UNCAPPED_BASELINE","SETUP_ONE","SETUP_ONE_PLUS_SCALE","MAX1","MAX2"],"research_only":True}
(OUT/"research_manifest.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
print(pd.DataFrame(out).to_string(index=False))

