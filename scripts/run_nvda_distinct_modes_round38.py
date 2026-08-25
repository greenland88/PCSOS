"""Round 38: NVDA exhaustion/divergence/consolidation mode discovery."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"
def ep(x):
 x=x.sort_values("trade_date").copy(); x["episode_id"]=(x.trade_date.diff().dt.days.fillna(999)>10).cumsum(); return x.groupby("episode_id",as_index=False).head(1)
def m(x):
 x=ep(x); n=x.loc[x.realized_pnl<0,"realized_pnl"].sum(); p=x.loc[x.realized_pnl>0,"realized_pnl"].sum(); return {"episodes":int(len(x)),"pnl":float(x.realized_pnl.sum()),"expectancy":float(x.realized_pnl.mean()) if len(x) else None,"profit_factor":float(p/abs(n)) if n else None,"stop_rate":float(x.stopped.mean()) if len(x) else None,"tail_losses":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.year.dropna().unique())}
def run():
 d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet").copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d[(d.ticker=="NVDA")&d.executable_pcs&d.trade_date.between("2020-01-02","2023-12-31")].copy(); atr=d.nvda_atr14.median()
 masks={"DOWN_DAY_EXHAUSTION_RECOVERY":(d.consecutive_down_days>=3)&(d.nvda_ret5>0),"RELATIVE_STRENGTH_DIVERGENCE":(d.nvda_ret20<=0)&(d.qqq_ret20>0)&(d.nvda_ret5>0),"LOW_VOLATILITY_CONSOLIDATION_RECOVERY":(d.nvda_atr14<=atr)&(d.nvda_ret20.abs()<.10)&(d.nvda_ret5>0),"PRICE_STRENGTH_WITHOUT_MARKET_CONFIRMATION":(d.nvda_ret5>0)&(d.nvda_relative_strength20>0)&(d.qqq_close_vs_sma50<=0)}
 rows=[]; yearly=[]
 for name,mask in masks.items():
  x=d[mask]; rows.append({"mode_id":name,"qualifying_dates":int(len(x)),**m(x)})
  for y,g in x.groupby(x.trade_date.dt.year): yearly.append({"mode_id":name,"year":int(y),**m(g)})
 pd.DataFrame(rows).to_csv(OUT/"v2_round38_distinct_modes.csv",index=False); pd.DataFrame(yearly).to_csv(OUT/"v2_round38_distinct_modes_yearly.csv",index=False)
 out={"module":"pcs.research.nvda_distinct_modes_round38","version":"1.0","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","input_rows":int(len(d)),"pit_feature_date_equals_trade_date":bool((d.date==d.trade_date).all()),"modes":[r["mode_id"] for r in rows],"validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"reason_codes":["NVDA_ONLY","FROZEN_623_DATE_TRAIN_UNIVERSE","PIT_SAFE","MATERIALLY_DIFFERENT_MODE_SCREEN","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}
 (OUT/"v2_round38_manifest.json").write_text(json.dumps(out,indent=2),encoding="utf-8"); return out
if __name__=="__main__": print(json.dumps(run(),indent=2))
