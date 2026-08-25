"""Round 35: evidence-derived NVDA transition modes, TRAIN descriptive only."""
from pathlib import Path
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"
BAD={"NORMAL_LOSS","STOP_LOSS","TAIL_LOSS"}

def episodes(x):
    x=x.sort_values("trade_date").copy(); x["episode_id"]=(x.trade_date.diff().dt.days.fillna(999)>10).cumsum(); return x.groupby("episode_id",as_index=False).head(1)
def metric(x):
    x=episodes(x); neg=x.loc[x.realized_pnl<0,"realized_pnl"].sum(); pos=x.loc[x.realized_pnl>0,"realized_pnl"].sum()
    return {"episodes":int(len(x)),"pnl":float(x.realized_pnl.sum()),"expectancy":float(x.realized_pnl.mean()) if len(x) else None,"profit_factor":float(pos/abs(neg)) if neg else None,"stop_rate":float(x.stopped.mean()) if len(x) else None,"normal_losses":int((x.outcome_class=="NORMAL_LOSS").sum()),"stop_losses":int((x.outcome_class=="STOP_LOSS").sum()),"tail_losses":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.year.dropna().unique())}

def run():
    d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet").copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d[(d.ticker=="NVDA")&d.trade_date.between("2020-01-02","2023-12-31")].sort_values("trade_date").copy()
    atr=d.nvda_atr14.median()
    for col in ["nvda_drawdown20","nvda_close_vs_sma20","nvda_close_vs_sma50","nvda_atr14","nvda_ret20","consecutive_down_days"]: d["prior_"+col]=d[col].shift(1)
    masks={
      "POST_SELLOFF_STABILIZATION":(d.prior_nvda_drawdown20<=-.15)&(d.nvda_ret5>0)&(d.consecutive_down_days==0),
      "SUPPORT_RECLAIM_TRANSITION":(d.prior_nvda_close_vs_sma20<=0)&(d.nvda_close_vs_sma20>0)&(d.prior_nvda_drawdown20<0),
      "RANGE_TO_STRENGTH_TRANSITION":(d.prior_nvda_ret20.abs()<.10)&(d.nvda_ret5>0)&(d.nvda_relative_strength20>0),
      "VOLATILITY_CONTRACTION_RECOVERY":(d.prior_nvda_atr14>atr)&(d.nvda_atr14<=atr)&(d.nvda_ret5>0),
    }
    rows=[]; bad_rows=[]; yearly=[]
    for name,mask in masks.items():
        x=d[mask & d.executable_pcs]; rows.append({"mode_id":name,"qualifying_dates":int(len(x)),**metric(x)})
        for y,g in x.groupby(x.trade_date.dt.year): yearly.append({"mode_id":name,"year":int(y),**metric(g)})
        outside=d[d.executable_pcs & ~mask]; bad_rows.append({"mode_id":name,"mode_episodes":int(len(episodes(x))),"bad_cases_inside":int(episodes(x).outcome_class.isin(BAD).sum()),"bad_rate_inside":float(x.outcome_class.isin(BAD).mean()) if len(x) else None,"bad_rate_outside":float(outside.outcome_class.isin(BAD).mean()) if len(outside) else None,"conclusion":"NO_RELIABLE_FILTER"})
    pd.DataFrame(rows).to_csv(OUT/"v2_round35_transition_modes.csv",index=False); pd.DataFrame(yearly).to_csv(OUT/"v2_round35_transition_modes_yearly.csv",index=False); pd.DataFrame(bad_rows).to_csv(OUT/"v2_round35_transition_bad_cases.csv",index=False)
    out={"module":"pcs.research.nvda_transition_mode_round35","version":"1.1","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","calendar_rows":int(len(d)),"input_rows":int(d.executable_pcs.sum()),"pit_feature_date_equals_trade_date":bool((d.date==d.trade_date).all()),"modes":[r["mode_id"] for r in rows],"validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"reason_codes":["NVDA_ONLY","FULL_PIT_CALENDAR_BEFORE_EXECUTABLE_FILTER","TRAIN_ONLY","PIT_SAFE_PRIOR_DAY_TRANSITIONS","MATERIALLY_DIFFERENT_MODE_SCREEN","BAD_CASE_AUDIT","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}
    (OUT/"v2_round35_manifest.json").write_text(json.dumps(out,indent=2),encoding="utf-8"); return out
if __name__=="__main__": print(json.dumps(run(),indent=2))
