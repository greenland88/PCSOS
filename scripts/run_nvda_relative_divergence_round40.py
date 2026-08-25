"""Round 40: relative-divergence independence from frozen NVDA families."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"
BAD={"NORMAL_LOSS","STOP_LOSS","TAIL_LOSS"}
def ep(x):
 x=x.sort_values("trade_date").copy(); x["episode_id"]=(x.trade_date.diff().dt.days.fillna(999)>10).cumsum(); return x.groupby("episode_id",as_index=False).head(1)
def m(x):
 x=ep(x); n=x.loc[x.realized_pnl<0,"realized_pnl"].sum(); p=x.loc[x.realized_pnl>0,"realized_pnl"].sum(); return {"episodes":int(len(x)),"pnl":float(x.realized_pnl.sum()),"expectancy":float(x.realized_pnl.mean()) if len(x) else None,"profit_factor":float(p/abs(n)) if n else None,"stop_rate":float(x.stopped.mean()) if len(x) else None,"bad_cases":int(x.outcome_class.isin(BAD).sum()),"tail_losses":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.year.dropna().unique())}
def run():
 d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet").copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d[(d.ticker=="NVDA")&d.executable_pcs&d.trade_date.between("2020-01-02","2023-12-31")].copy()
 trend=(d.nvda_close_vs_sma200>0)&(d.nvda_volume_rel20>1)&(d.nvda_ret5>0); recovery=(d.nvda_close_vs_sma200>0)&(d.nvda_ret20<0)&(d.nvda_ret5>0); candidate=(d.nvda_ret20<=0)&(d.qqq_ret20>0)&(d.nvda_ret5>0); independent=candidate&~trend&~recovery
 rows=[]
 for name,mask in {"RELATIVE_DIVERGENCE_ALL":candidate,"RELATIVE_DIVERGENCE_OUTSIDE_FROZEN_FAMILIES":independent,"OVERLAP_TREND":candidate&trend,"OVERLAP_RECOVERY":candidate&recovery}.items(): rows.append({"population":name,"rows":int(mask.sum()),**m(d[mask])})
 e=ep(d[candidate]); overlap={"candidate_episodes":int(len(e)),"trend_overlap_episodes":int(len(ep(d[candidate&trend]))),"recovery_overlap_episodes":int(len(ep(d[candidate&recovery]))),"independent_episodes":int(len(ep(d[independent])))}
 out={"module":"pcs.research.nvda_relative_divergence_round40","version":"1.0","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","input_rows":int(len(d)),"pit_feature_date_equals_trade_date":bool((d.date==d.trade_date).all()),"populations":rows,"episode_overlap":overlap,"decision":"RESEARCH_PROMISING_BUT_INSUFFICIENT","validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"reason_codes":["NVDA_ONLY","TRAIN_ONLY","FROZEN_FAMILY_INDEPENDENCE_AUDIT","NO_THRESHOLD_TUNING","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}
 pd.DataFrame(rows).to_csv(OUT/"v2_round40_relative_divergence_independence.csv",index=False); (OUT/"v2_round40_relative_divergence_independence.json").write_text(json.dumps(out,indent=2),encoding="utf-8"); return out
if __name__=="__main__": print(json.dumps(run(),indent=2))
