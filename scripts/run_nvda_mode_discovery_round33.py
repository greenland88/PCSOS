"""Predeclared component-ablation screen for NVDA mode-discovery leads."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"

def ep(x):
    x = x.sort_values("trade_date").copy()
    x["episode_id"] = (x.trade_date.diff().dt.days.fillna(999) > 10).cumsum()
    return x.groupby("episode_id", as_index=False).head(1)

def m(x):
    x = ep(x); neg=x.loc[x.realized_pnl<0,"realized_pnl"].sum(); pos=x.loc[x.realized_pnl>0,"realized_pnl"].sum()
    return {"episodes":int(len(x)),"pnl":float(x.realized_pnl.sum()),"expectancy":float(x.realized_pnl.mean()) if len(x) else None,"profit_factor":float(pos/abs(neg)) if neg else None,"stop_rate":float(x.stopped.mean()) if len(x) else None,"tail_losses":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.year.dropna().unique())}

def run():
    d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet").copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d[(d.ticker=="NVDA")&d.executable_pcs&d.trade_date.between("2020-01-02","2023-12-31")].copy()
    atr=d.nvda_atr14.median()
    masks={
      "MARKET_CONFIRMED_BASE": (d.qqq_close_vs_sma50>0)&(d.nvda_relative_strength20>0)&(d.nvda_ret5>0),
      "MARKET_CONFIRMED_NO_MARKET": (d.nvda_relative_strength20>0)&(d.nvda_ret5>0),
      "MARKET_CONFIRMED_NO_RELATIVE_STRENGTH": (d.qqq_close_vs_sma50>0)&(d.nvda_ret5>0),
      "MARKET_CONFIRMED_NO_MOMENTUM": (d.qqq_close_vs_sma50>0)&(d.nvda_relative_strength20>0),
      "VOLATILITY_BASE": (d.nvda_atr14>atr)&(d.nvda_ret5>0)&(d.nvda_volume_rel20>1),
      "VOLATILITY_NO_ATR": (d.nvda_ret5>0)&(d.nvda_volume_rel20>1),
      "VOLATILITY_NO_VOLUME": (d.nvda_atr14>atr)&(d.nvda_ret5>0),
      "VOLATILITY_NO_MOMENTUM": (d.nvda_atr14>atr)&(d.nvda_volume_rel20>1),
    }
    rows=[]
    for name, mask in masks.items(): rows.append({"ablation_id":name,"qualifying_dates":int(mask.sum()),**m(d[mask])})
    pd.DataFrame(rows).to_csv(OUT/"v2_round33_component_ablation.csv",index=False)
    result={"module":"pcs.research.nvda_mode_discovery_round33","version":"1.0","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","input_rows":int(len(d)),"pit_feature_date_equals_trade_date":bool((d.date==d.trade_date).all()),"ablation_policy":"predeclared component removal; no threshold search","validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"reason_codes":["NVDA_ONLY","TRAIN_ONLY","STRUCTURAL_SENSITIVITY","NO_THRESHOLD_TUNING","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}
    (OUT/"v2_round33_manifest.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result
if __name__=="__main__": print(json.dumps(run(),indent=2))
