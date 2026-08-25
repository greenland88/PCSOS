"""Round 49: outcome-conditioned PIT feature profile for NVDA TRAIN."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"
FEATURES=["nvda_ret5","nvda_ret10","nvda_ret20","nvda_drawdown20","nvda_volume_rel20","nvda_close_vs_sma20","nvda_close_vs_sma50","nvda_close_vs_sma200","qqq_ret5","qqq_ret20","qqq_close_vs_sma50","nvda_relative_strength20","consecutive_down_days","nvda_atr14"]
def run():
 d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet").copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d[(d.ticker=="NVDA")&d.executable_pcs&d.trade_date.between("2020-01-02","2023-12-31")].copy(); profitable=d.outcome_class.isin(["GOOD_WIN","SMALL_WIN"]); bad=d.outcome_class.isin(["NORMAL_LOSS","STOP_LOSS","TAIL_LOSS"]); rows=[]
 for f in FEATURES:
  for group,mask in [("PROFITABLE",profitable),("NORMAL_LOSS",d.outcome_class=="NORMAL_LOSS"),("STOP_LOSS",d.outcome_class=="STOP_LOSS"),("TAIL_LOSS",d.outcome_class=="TAIL_LOSS")]:
   x=d.loc[mask,f].dropna(); rows.append({"feature":f,"outcome_group":group,"count":int(len(x)),"mean":float(x.mean()) if len(x) else None,"median":float(x.median()) if len(x) else None,"min":float(x.min()) if len(x) else None,"max":float(x.max()) if len(x) else None})
 pd.DataFrame(rows).to_csv(OUT/"v2_round49_outcome_feature_profiles.csv",index=False)
 summary=[]
 for f in FEATURES:
  p=d.loc[profitable,f].dropna(); b=d.loc[bad,f].dropna(); summary.append({"feature":f,"profitable_median":float(p.median()) if len(p) else None,"bad_median":float(b.median()) if len(b) else None,"median_difference":float(p.median()-b.median()) if len(p) and len(b) else None,"directional_followup":"DESCRIPTIVE_ONLY"})
 pd.DataFrame(summary).to_csv(OUT/"v2_round49_outcome_feature_median_differences.csv",index=False)
 out={"module":"pcs.research.nvda_outcome_feature_profile_round49","version":"1.0","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","input_rows":int(len(d)),"pit_feature_date_equals_trade_date":bool((d.date==d.trade_date).all()),"outcome_counts":d.outcome_class.value_counts().to_dict(),"feature_count":len(FEATURES),"tail_loss_rows":int((d.outcome_class=="TAIL_LOSS").sum()),"decision":"DESCRIPTIVE_FEATURE_AUDIT_ONLY","validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"reason_codes":["NVDA_ONLY","TRAIN_ONLY","PIT_SAFE","OUTCOME_CONDITIONED_FEATURE_PROFILE","NO_THRESHOLD_MINING","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}; (OUT/"v2_round49_manifest.json").write_text(json.dumps(out,indent=2),encoding="utf-8"); return out
if __name__=="__main__": print(json.dumps(run(),indent=2))
