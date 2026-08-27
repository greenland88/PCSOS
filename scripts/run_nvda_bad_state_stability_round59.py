"""Round 59: episode/year stability for NVDA BAD_STATE candidates."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"
BAD={"NORMAL_LOSS","STOP_LOSS","TAIL_LOSS"}
def ep(x):
 x=x.sort_values("trade_date").copy(); x["episode_id"]=(x.trade_date.diff().dt.days.fillna(999)>10).cumsum(); return x.groupby("episode_id",as_index=False).head(1)
def stats(x):
 x=ep(x); b=x.outcome_class.isin(BAD); return {"episodes":int(len(x)),"bad_cases":int(b.sum()),"bad_rate":float(b.mean()) if len(x) else None,"pnl":float(x.realized_pnl.sum()),"stop_rate":float(x.stopped.mean()) if len(x) else None,"tail_losses":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.year.dropna().unique())}
def run():
 d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet").copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d[(d.ticker=="NVDA")&d.executable_pcs&d.trade_date.between("2020-01-02","2023-12-31")].copy(); atr=d.nvda_atr14.median(); states={"ANY_DOWN_DAY":d.consecutive_down_days>=1,"ELEVATED_ATR":d.nvda_atr14>atr,"DOWN_DAY_AND_ELEVATED_ATR":(d.consecutive_down_days>=1)&(d.nvda_atr14>atr)}; rows=[]
 for name,mask in states.items():
  inside=d[mask]; outside=d[~mask]; e=ep(inside); rows.append({"state_id":name,"population":"inside","scope":"full","metrics":stats(inside)}); rows.append({"state_id":name,"population":"outside","scope":"full","metrics":stats(outside)})
  for y in sorted(int(y) for y in e.year.dropna().unique()): rows.append({"state_id":name,"population":"inside","scope":f"leave_out_{y}","metrics":stats(e[e.year!=y])})
 out={"module":"pcs.research.nvda_bad_state_stability_round59","version":"1.0","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","input_rows":int(len(d)),"pit_feature_date_equals_trade_date":bool((d.date==d.trade_date).all()),"median_atr":float(atr),"states":rows,"decision":"NO_RELIABLE_FILTER","validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"reason_codes":["NVDA_ONLY","TRAIN_ONLY","BAD_STATE_EPISODE_STABILITY","LEAVE_ONE_YEAR_OUT","NO_FORCED_NO_TRADE","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}; (OUT/"v2_round59_bad_state_stability.json").write_text(json.dumps(out,indent=2),encoding="utf-8"); return out
if __name__=="__main__": print(json.dumps(run(),indent=2))
