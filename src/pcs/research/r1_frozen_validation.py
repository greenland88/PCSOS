"""Frozen R1 V1 research specification and prospective signal contract."""
from pathlib import Path
import json
from datetime import datetime, timezone

R1_FROZEN_V1 = {
    "version":"R1_FROZEN_V1",
    "calculation_version":"risk_layer_validation_v1_existing",
    "development_symbols":["NVDA","QQQ","AMZN","TSLA"],
    "tier1_features":["atr_expansion","drawdown20","down_streak"],
    "tier2_features":["atr_pct","move5_atr"],
    "formulas":{
        "atr_pct":"ATR14 / close",
        "atr_expansion":"ATR14 / trailing 60-trading-day median ATR14",
        "drawdown20":"1 - close / rolling 20-trading-day maximum close",
        "down_streak":"consecutive strictly lower closes ending on date",
        "move5_atr":"abs(close - close_5_trading_days_ago) / ATR14",
        "percentile":"expanding historical percentile: count(prior values < current) / count(prior values)",
        "score":"mean(Tier1 percentiles) * 0.67 + mean(Tier2 percentiles) * 0.33",
    },
    "min_history":50,
    "state_thresholds":{"R1_NORMAL":"score < 0.25","R2_ELEVATED":"0.25 <= score < 0.50","R3_DEFENSIVE":"0.50 <= score < 0.75","R4_HIGH_RISK":"score >= 0.75"},
    "missing_data":"state unavailable until every feature has at least 50 prior observations",
    "lookahead":"current row is scored before it is appended to percentile history",
}

PROSPECTIVE_FIELDS=["timestamp","ticker","r1_state","risk_score","trend_state","structural_risk","regime","atr14","price","event_status","data_version","r1_version"]

def write_frozen_spec(output_dir="research_outputs"):
    p=Path(output_dir); p.mkdir(parents=True,exist_ok=True)
    (p/"r1_frozen_v1_spec.json").write_text(json.dumps(R1_FROZEN_V1,indent=2),encoding="utf-8")
    import pandas as pd
    pd.DataFrame(columns=PROSPECTIVE_FIELDS).to_csv(p/"r1_prospective_signal_log_schema.csv",index=False)
    return R1_FROZEN_V1

if __name__=="__main__": print(write_frozen_spec())
