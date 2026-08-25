"""Round 57: NVDA bearish/rebound and volatility-contraction modes."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"
def ep(x):
 x=x.sort_values("trade_date").copy(); x["episode_id"]=(x.trade_date.diff().dt.days.fillna(999)>10).cumsum(); return x.groupby("episode_id",as_index=False).head(1)
def m(x):
 x=ep(x); n=x.loc[x.realized_pnl<0,"realized_pnl"].sum(); p=x.loc[x.realized_pnl>0,"realized_pnl"].sum(); return {"episodes":int(len(x)),"pnl":float(x.realized_pnl.sum()),"expectancy":float(x.realized_pnl.mean()) if len(x) else None,"profit_factor":float(p/abs(n)) if n else None,"stop_rate":float(x.stopped.mean()) if len(x) else None,"bad_cases":int(x.outcome_class.isin(["NORMAL_LOSS","STOP_LOSS","TAIL_LOSS"]).sum()),"tail_losses":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.year.dropna().unique())}
def run():
 d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet").copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d[(d.ticker=="NVDA")&d.executable_pcs&d.trade_date.between("2020-01-02","2023-12-31")].copy(); atr=d.nvda_atr14.median(); trend=(d.nvda_close_vs_sma200>0)&(d.nvda_volume_rel20>1)&(d.nvda_ret5>0); recovery=(d.nvda_close_vs_sma200>0)&(d.nvda_ret20<0)&(d.nvda_ret5>0); modes={"DOWNTREND_RECOVERY":(d.nvda_close_vs_sma200<0)&(d.nvda_ret20<0)&(d.nvda_ret5>0),"BEAR_MARKET_REBOUND":(d.nvda_close_vs_sma200<0)&(d.nvda_ret5>0)&(d.nvda_volume_rel20>1),"VOLATILITY_CONTRACTION_RECOVERY":(d.nvda_close_vs_sma200>0)&(d.nvda_ret20<0)&(d.nvda_ret5>0)&(d.nvda_atr14<=atr),"EXTREME_VOLATILITY_CAUTION":d.nvda_atr14>d.nvda_atr14.quantile(.75)}; rows=[]
 for name,mask in modes.items():
  for pop,pm in [("ALL",mask),("OUTSIDE_FROZEN_FAMILIES",mask&~trend&~recovery),("OVERLAP_TREND",mask&trend),("OVERLAP_RECOVERY",mask&recovery)]: pm=pm.reindex(d.index,fill_value=False); rows.append({"mode_id":name,"population":pop,"rows":int(pm.sum()),**m(d[pm])})
 pd.DataFrame(rows).to_csv(OUT/"v2_round57_bear_rebound_independence.csv",index=False); out={"module":"pcs.research.nvda_bear_rebound_round57","version":"1.0","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","input_rows":int(len(d)),"pit_feature_date_equals_trade_date":bool((d.date==d.trade_date).all()),"populations":rows,"decision":"RESEARCH_PROMISING_BUT_INSUFFICIENT","validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"reason_codes":["NVDA_ONLY","TRAIN_ONLY","BEAR_REBOUND_MODE_SCREEN","FROZEN_FAMILY_INDEPENDENCE_AUDIT","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}; (OUT/"v2_round57_manifest.json").write_text(json.dumps(out,indent=2),encoding="utf-8"); return out
if __name__=="__main__": print(json.dumps(run(),indent=2))
