"""Round 36 robustness for the two positive Round 35 transition leads."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"
def ep(x):
 x=x.sort_values("trade_date").copy(); x["episode_id"]=(x.trade_date.diff().dt.days.fillna(999)>10).cumsum(); return x.groupby("episode_id",as_index=False).head(1)
def m(x):
 x=ep(x); n=x.loc[x.realized_pnl<0,"realized_pnl"].sum(); p=x.loc[x.realized_pnl>0,"realized_pnl"].sum(); return {"episodes":int(len(x)),"pnl":float(x.realized_pnl.sum()),"expectancy":float(x.realized_pnl.mean()) if len(x) else None,"profit_factor":float(p/abs(n)) if n else None,"stop_rate":float(x.stopped.mean()) if len(x) else None,"tail_losses":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.year.dropna().unique())}
def run():
 d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet").copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d[(d.ticker=="NVDA")&d.trade_date.between("2020-01-02","2023-12-31")].sort_values("trade_date").copy(); d["prior_nvda_close_vs_sma20"]=d.nvda_close_vs_sma20.shift(1); d["prior_nvda_drawdown20"]=d.nvda_drawdown20.shift(1); d["prior_nvda_ret20"]=d.nvda_ret20.shift(1); d=d[d.executable_pcs].copy()
 masks={"SUPPORT_RECLAIM_TRANSITION":(d.prior_nvda_close_vs_sma20<=0)&(d.nvda_close_vs_sma20>0)&(d.prior_nvda_drawdown20<0),"RANGE_TO_STRENGTH_TRANSITION":(d.prior_nvda_ret20.abs()<.10)&(d.nvda_ret5>0)&(d.nvda_relative_strength20>0)}; rows=[]; concentration=[]
 for name,mask in masks.items():
  x=d[mask]; e=ep(x); rows.append({"mode_id":name,"full_sample":m(x)}); 
  for y in sorted(int(y) for y in e.year.dropna().unique()): rows.append({"mode_id":name,"left_out_year":y,"loo":m(e[e.year!=y])})
  r=e.sort_values("realized_pnl",ascending=False); total=e.realized_pnl.sum(); concentration.append({"mode_id":name,"episodes":int(len(e)),"top_1_share":float(r.head(1).realized_pnl.sum()/total) if total else None,"top_3_share":float(r.head(3).realized_pnl.sum()/total) if total else None,"worst_trade":float(e.realized_pnl.min()) if len(e) else None})
 (OUT/"v2_round36_transition_robustness.json").write_text(json.dumps({"module":"pcs.research.nvda_transition_robustness_round36","version":"1.0","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","input_rows":int(len(d)),"pit_feature_date_equals_trade_date":bool((d.date==d.trade_date).all()),"leave_one_year_out":rows,"pnl_concentration":concentration,"validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"reason_codes":["NVDA_ONLY","TRAIN_ONLY","INDEPENDENT_EPISODE_ANALYSIS","LEAVE_ONE_YEAR_OUT","PNL_CONCENTRATION","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]},indent=2),encoding="utf-8")
 return rows,concentration
if __name__=="__main__": print(json.dumps(run(),indent=2))
