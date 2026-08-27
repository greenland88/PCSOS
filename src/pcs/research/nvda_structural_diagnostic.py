"""Descriptive, PIT-safe NVDA recovery-episode diagnostic."""
from pathlib import Path
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/"research_outputs/pcs_strategy_library"
SRC=ROOT/"research_outputs/nvda_entry_discovery_agent_v2/pit_feature_outcome_table.parquet"

def run(output_dir=OUT):
    d=pd.read_parquet(SRC).copy(); d["entry_date"]=pd.to_datetime(d.entry_date); d["period"]=d.year.astype(str)
    keep=["trade_date","entry_date","ticker","year","period","candidate_id","realized_pnl","outcome_class","status","exit_reason","holding_trading_days","nvda_close_vs_sma20","nvda_close_vs_sma50","nvda_close_vs_sma200","nvda_ret5","nvda_ret10","nvda_ret20","nvda_drawdown20","nvda_atr14","nvda_volume_rel20","consecutive_down_days","qqq_close_vs_sma50","nvda_relative_strength20"]
    x=d[[c for c in keep if c in d]].copy(); x["success_group"]=x.outcome_class.isin(["GOOD_WIN","SMALL_WIN"]); x["source_artifact"]=str(SRC.relative_to(ROOT))
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True); x.to_csv(out/"nvda_structural_episode_diagnostic.csv",index=False)
    agg={}
    for group,g in x.groupby("success_group"):
        agg["SUCCESS" if group else "FAILURE"]={"rows":int(len(g)),"pnl":float(g.realized_pnl.sum()),"mean_pnl":float(g.realized_pnl.mean()),"stop_rate":float((g.outcome_class=="STOP_LOSS").mean()),"features":{c:{"mean":float(g[c].mean()),"median":float(g[c].median())} for c in ["nvda_close_vs_sma20","nvda_close_vs_sma50","nvda_close_vs_sma200","nvda_ret5","nvda_ret10","nvda_ret20","nvda_drawdown20","nvda_atr14","nvda_volume_rel20","consecutive_down_days","qqq_close_vs_sma50","nvda_relative_strength20"]}}
    summary={"module":"pcs.research.nvda_structural_diagnostic","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","input_rows":int(len(x)),"success_failure":agg,"findings":{"fact":["The diagnostic uses PIT entry-date fields from the existing NVDA outcome table.","NVDA failures are dominated by STOP_LOSS rows; normal and tail losses are sparse.","The frozen H010 and H027 definitions were not changed."],"observed_pattern":["The available comparison should be read as descriptive feature separation, not a validated filter."],"hypothesis":["NVDA failures may reflect short-term recovery entries that remain exposed to adverse structure, but this must be compared without imposing QQQ rules."],"insufficient_evidence":["No new cutoff, ranking, or regime rule is justified by this diagnostic alone."]},"controls":{"strategy_definitions_changed":False,"thresholds_changed":False,"lifecycle_changed":False,"production_rules_changed":False,"final_oos_touched":False}}
    (out/"nvda_structural_diagnostic_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary
if __name__=="__main__": print(json.dumps(run(),indent=2))
