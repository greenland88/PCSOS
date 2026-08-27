"""Round 45: NVDA range-consolidation breakout mode, TRAIN descriptive only."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"
def ep(x):
 x=x.sort_values("trade_date").copy(); x["episode_id"]=(x.trade_date.diff().dt.days.fillna(999)>10).cumsum(); return x.groupby("episode_id",as_index=False).head(1)
def m(x):
 x=ep(x); n=x.loc[x.realized_pnl<0,"realized_pnl"].sum(); p=x.loc[x.realized_pnl>0,"realized_pnl"].sum(); return {"episodes":int(len(x)),"pnl":float(x.realized_pnl.sum()),"expectancy":float(x.realized_pnl.mean()) if len(x) else None,"profit_factor":float(p/abs(n)) if n else None,"stop_rate":float(x.stopped.mean()) if len(x) else None,"bad_cases":int(x.outcome_class.isin(["NORMAL_LOSS","STOP_LOSS","TAIL_LOSS"]).sum()),"tail_losses":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.year.dropna().unique())}
def run():
 d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet").copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d[(d.ticker=="NVDA")&d.executable_pcs&d.trade_date.between("2020-01-02","2023-12-31")].copy(); trend=(d.nvda_close_vs_sma200>0)&(d.nvda_volume_rel20>1)&(d.nvda_ret5>0); recovery=(d.nvda_close_vs_sma200>0)&(d.nvda_ret20<0)&(d.nvda_ret5>0); mask=(d.nvda_ret20.abs()<=.05)&(d.nvda_ret5>0)&(d.nvda_volume_rel20>=1); rows=[]; yearly=[]
 for name,pm in [("ALL",mask),("OUTSIDE_FROZEN_FAMILIES",mask&~trend&~recovery),("OVERLAP_TREND",mask&trend),("OVERLAP_RECOVERY",mask&recovery)]: pm=pm.reindex(d.index,fill_value=False); rows.append({"population":name,"qualifying_dates":int(pm.sum()),**m(d[pm])});
 for y,g in d[mask].groupby(d[mask].trade_date.dt.year): yearly.append({"year":int(y),**m(g)})
 pd.DataFrame(rows).to_csv(OUT/"v2_round45_range_breakout_independence.csv",index=False); pd.DataFrame(yearly).to_csv(OUT/"v2_round45_range_breakout_yearly.csv",index=False)
 out={"module":"pcs.research.nvda_range_breakout_round45","version":"1.0","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","input_rows":int(len(d)),"pit_feature_date_equals_trade_date":bool((d.date==d.trade_date).all()),"decision":"RESEARCH_PROMISING_BUT_INSUFFICIENT","validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"reason_codes":["NVDA_ONLY","FROZEN_623_DATE_TRAIN_UNIVERSE","PIT_SAFE","RANGE_CONSOLIDATION_BREAKOUT","FROZEN_FAMILY_INDEPENDENCE_AUDIT","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}; (OUT/"v2_round45_manifest.json").write_text(json.dumps(out,indent=2),encoding="utf-8"); return out
if __name__=="__main__": print(json.dumps(run(),indent=2))
