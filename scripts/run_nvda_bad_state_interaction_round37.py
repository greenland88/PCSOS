"""Round 37: predeclared NVDA BAD_STATE interactions, TRAIN descriptive only."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"
BAD={"NORMAL_LOSS","STOP_LOSS","TAIL_LOSS"}
def ep(x):
 x=x.sort_values("trade_date").copy(); x["episode_id"]=(x.trade_date.diff().dt.days.fillna(999)>10).cumsum(); return x.groupby("episode_id",as_index=False).head(1)
def run():
 d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet").copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d[(d.ticker=="NVDA")&d.executable_pcs&d.trade_date.between("2020-01-02","2023-12-31")].copy(); bad=d.outcome_class.isin(BAD)
 states={"LOW_PARTICIPATION":d.nvda_volume_rel20<1,"LONG_TERM_WEAKNESS":d.nvda_close_vs_sma200<=0,"DOWNSIDE_ACCELERATION":(d.nvda_ret5<0)&(d.nvda_ret20<0),"DEEP_SELL_OFF":d.nvda_drawdown20<=-.15,"MARKET_UNCONFIRMED":d.qqq_close_vs_sma50<=0,"NEGATIVE_RELATIVE_STRENGTH":d.nvda_relative_strength20<=0}
 combos={"LOW_PARTICIPATION_AND_WEAKNESS":states["LOW_PARTICIPATION"]&states["LONG_TERM_WEAKNESS"],"LOW_PARTICIPATION_AND_MARKET_UNCONFIRMED":states["LOW_PARTICIPATION"]&states["MARKET_UNCONFIRMED"],"DEEP_SELLOFF_AND_MARKET_UNCONFIRMED":states["DEEP_SELL_OFF"]&states["MARKET_UNCONFIRMED"],"DOWNSIDE_ACCELERATION_AND_NEGATIVE_RELATIVE_STRENGTH":states["DOWNSIDE_ACCELERATION"]&states["NEGATIVE_RELATIVE_STRENGTH"],"WEAKNESS_AND_DEEP_SELLOFF":states["LONG_TERM_WEAKNESS"]&states["DEEP_SELL_OFF"]}
 rows=[]; yearly=[]
 for name,mask in combos.items():
  x=d[mask]; y=d[~mask]; ex=ep(x); rows.append({"state_id":name,"rows_inside":int(len(x)),"episodes_inside":int(len(ex)),"bad_inside":int(bad[mask].sum()),"normal_loss_inside":int((d.outcome_class.eq("NORMAL_LOSS")&mask).sum()),"stop_loss_inside":int((d.outcome_class.eq("STOP_LOSS")&mask).sum()),"tail_loss_inside":int((d.outcome_class.eq("TAIL_LOSS")&mask).sum()),"bad_rate_inside":float(bad[mask].mean()) if len(x) else None,"bad_rate_outside":float(bad[~mask].mean()) if len(y) else None,"pnl_inside":float(x.realized_pnl.sum()),"pnl_outside":float(y.realized_pnl.sum()),"conclusion":"NO_RELIABLE_FILTER"})
  for year,g in x.groupby(x.trade_date.dt.year): yearly.append({"state_id":name,"year":int(year),"rows_inside":int(len(g)),"bad_inside":int(bad.loc[g.index].sum()),"stop_loss_inside":int((g.outcome_class=="STOP_LOSS").sum()),"tail_loss_inside":int((g.outcome_class=="TAIL_LOSS").sum()),"bad_rate_inside":float(bad.loc[g.index].mean()) if len(g) else None})
 pd.DataFrame(rows).to_csv(OUT/"v2_round37_bad_state_interactions.csv",index=False); pd.DataFrame(yearly).to_csv(OUT/"v2_round37_bad_state_interactions_yearly.csv",index=False)
 out={"module":"pcs.research.nvda_bad_state_interaction_round37","version":"1.0","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","input_rows":int(len(d)),"pit_feature_date_equals_trade_date":bool((d.date==d.trade_date).all()),"outcome_counts":d.outcome_class.value_counts().to_dict(),"conclusion":"NO_RELIABLE_FILTER","validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"reason_codes":["NVDA_ONLY","FROZEN_623_DATE_TRAIN_UNIVERSE","PIT_SAFE","PREDECLARED_STATE_INTERACTIONS","BAD_CASES_DIRECTLY_COUNTED","NO_FORCED_NO_TRADE","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}
 (OUT/"v2_round37_manifest.json").write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); return out
if __name__=="__main__": print(json.dumps(run(),indent=2,default=str))
