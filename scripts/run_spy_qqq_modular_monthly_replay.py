"""Bounded, checkpointed research-only monthly selector/replay pilot."""
from pathlib import Path
import argparse, hashlib, json, os, time
import pandas as pd
import yaml
from pcs.data.access import PCSDataAccess
from pcs.research.credit_stop import load_quotes_canonical,load_spread_quotes_canonical,track_trade
from pcs.research.rules.core import evaluate_chain,resolve_scenario,RuleStatus,canonical_hash
from pcs.research.rules.registry import RULE_REGISTRY
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs/spy_qqq_modular_rule_research_20260821"
def daily(t):
 x=PCSDataAccess().read_prices(t);x["date"]=pd.to_datetime(x.date).dt.normalize();x=x.sort_values("date").drop_duplicates("date");p=x.close.shift(1);tr=pd.concat([x.high-x.low,(x.high-p).abs(),(x.low-p).abs()],axis=1).max(axis=1);x["atr"]=tr.rolling(14,min_periods=14).mean();return x
def select(q,row):
 q=q[(q.DTE.between(30,45))&(q["Call/Put"].eq("p"))].copy()
 if q.empty or pd.isna(row.atr):return []
 target=row.close-2.3*row.atr;out=[]
 for w in (5,10,2):
  shorts=q[q.Strike<row.close].assign(dist=lambda z:(z.Strike-target).abs()).sort_values(["dist","Strike"])
  for _,s in shorts.iterrows():
   z=q[q.Strike.eq(s.Strike-w)].sort_values("Strike")
   if len(z):out.append((s,z.iloc[0],w));break
 return out
def context(t,row,s,l,w):
 return {"ticker":t,"date":row.date,"expiration":pd.Timestamp(s["Expiry Date"]),"dte":int(s.DTE),"atr":float(row.atr) if pd.notna(row.atr) else None,"underlying_price":float(row.close),"short_strike":float(s.Strike),"long_strike":float(l.Strike),"credit":float(s["Bid Price"]-l["Ask Price"]),"width":w,"short_bid":float(s["Bid Price"]),"short_ask":float(s["Ask Price"]),"long_bid":float(l["Bid Price"]),"long_ask":float(l["Ask Price"]),"volume":float(s["Volume"]),"open_interest":float(s["Open Interest"])}
def one(t,month,scenario,boundary):
 c=yaml.safe_load((ROOT/"research_configs/pcs_rule_scenarios"/scenario).read_text());sc=resolve_scenario(c); ds=daily(t);lo=pd.Timestamp(month+"-01");hi=lo+pd.offsets.MonthEnd();ds=ds[ds.date.between(lo,hi)];led=[];cand=[];trades=[];totalrows=0
 for r in ds.itertuples():
  q,meta=load_quotes_canonical(t,r.date,r.date);totalrows+=meta["option_rows_loaded"];choices=select(q,r);selected=False
  for s,l,w in choices:
   cx=context(t,r,s,l,w)
   for mode in ("FULL_AUDIT","PRODUCTION_SHORT_CIRCUIT"):
    results=evaluate_chain(sc["entry_rule_chain"],RULE_REGISTRY,cx,mode); rec={"ticker":t,"date":r.date,"mode":mode,"scenario_id":sc["scenario_id"],"short_strike":cx["short_strike"],"long_strike":cx["long_strike"],"selected":False}
    for rule,res in results:rec[rule.rule_id]=res.status;rec[rule.rule_id+"_reason"]=";".join(res.reason_codes)
    cand.append(rec)
   available=[x for x in evaluate_chain(sc["entry_rule_chain"],RULE_REGISTRY,cx,"FULL_AUDIT") if x[0].rule_id not in {"liquidity_gate"}]
   ok=all(res.status==RuleStatus.PASS for _,res in available)
   if ok and not selected:
    selected=True;rec=cand[-2];rec["selected"]=True; end=min(pd.Timestamp(s["Expiry Date"]),boundary);marks,_=load_spread_quotes_canonical(t,r.date,end,s["Expiry Date"],[s.Strike,l.Strike]);path=track_trade({"date":r.date,"expiration":s["Expiry Date"],"short_strike":s.Strike,"long_strike":l.Strike},marks,s,l,cx["credit"]); events=[v for v in path["events"].values() if v is not None]; exit_date=min(events) if events else (marks["Trade Date"].max() if not marks.empty else pd.NaT);blocked=pd.Timestamp(s["Expiry Date"])>boundary and (pd.isna(exit_date) or exit_date>=boundary);trades.append({**cx,"candidate_id":canonical_hash([t,str(r.date),str(s["Expiry Date"]),float(s.Strike),float(l.Strike)])[:24],"exit_date":exit_date,"stop":path["exit_reason"]=="STOP","exit_reason":"FINAL_OOS_BOUNDARY_BLOCKED" if blocked else path["exit_reason"],"pnl":None if blocked else path["realized_pnl"],"planned_loss":cx["credit"]*100})
  led.append({"ticker":t,"date":r.date,"scenario_id":sc["scenario_id"],"option_rows":meta["option_rows_loaded"],"generated":len(choices),"selected":selected})
 return led,cand,trades,{"scenario":sc,"option_rows":totalrows}
def main():
 a=argparse.ArgumentParser();a.add_argument("--month",default="2025-01");a.add_argument("--full-train",action="store_true");a.add_argument("--validation",action="store_true");x=a.parse_args();OUT.mkdir(parents=True,exist_ok=True);alll=[];allc=[];allt=[];run=[]
 months=pd.period_range("2026-01","2026-05",freq="M").astype(str).tolist() if x.validation else ([x.month] if not x.full_train else pd.period_range("2020-02","2025-12",freq="M").astype(str).tolist());label="validation" if x.validation else "train" if x.full_train else "pilot";boundary=pd.Timestamp("2026-05-31" if x.validation else "2025-12-31")
 for m in months:
  for t in ("SPY","QQQ"):
   led,cand,tr,meta=one(t,m,"research_current_rules_available_context.yaml",boundary);alll+=led;allc+=cand;allt+=tr;run.append({"ticker":t,"month":m,**meta});(OUT/f"checkpoint_{label}_{t}_{m}.json").write_text(json.dumps(run[-1],default=str),encoding="utf8")
 pd.DataFrame(alll).to_parquet(OUT/f"{label}_daily_decision_ledger.parquet",index=False);pd.DataFrame(allc).to_parquet(OUT/f"{label}_candidate_gate_ledger.parquet",index=False);pd.DataFrame(allt).to_parquet(OUT/f"{label}_selected_lifecycle.parquet",index=False);pd.DataFrame(run).to_csv(OUT/f"{label}_run_metrics.csv",index=False)
 h=canonical_hash({"ledger":alll,"candidates":allc,"trades":allt});(OUT/f"{label}_manifest.json").write_text(json.dumps({"hash":h,"months":months,"final_oos_read":False,"single_worker":True},indent=2),encoding="utf8");print(json.dumps({"hash":h,"daily":len(alll),"candidate":len(allc),"selected":len(allt),"months":months},default=str))
if __name__=="__main__":main()
