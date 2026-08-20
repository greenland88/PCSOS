from pathlib import Path
import json
import pandas as pd
from pcs.research.stage4a_replay import audit_inputs

ROOT=Path("research_outputs/safe_strike_stage4a")
ROOT.mkdir(parents=True,exist_ok=True)
paths=list(Path("research_outputs/safe_strike_stage2/2.3ATR").glob("*.parquet"))
rows=[]
for p in paths:
 d=pd.read_parquet(p).head(5)
 a=audit_inputs(d)
 rows.append({"ticker":p.stem,"sample_rows":len(d),**a.to_dict(),"source":str(p),"safe_strike_atr":2.3})
out=pd.DataFrame(rows); out.to_csv(ROOT/"input_availability_audit.csv",index=False)
summary={"module":"stage4a_replay_adapter","version":"1.0","status":"BLOCKED_INCOMPLETE_HISTORICAL_CONTRACT","safe_strike_atr":2.3,"components_reused":["DecisionEngine","TradeCandidate","MarketRegimeEngine","LiquidityScorer","StrikeScorer","PositionSizer"],"adapter_added":"pcs.research.stage4a_replay","rows_audited":int(out.sample_rows.sum()),"missing_by_ticker":out.set_index('ticker').missing.to_dict() if len(out) else {},"stage4_ready":False,"live_configuration_changed":False}
(ROOT/"validation_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
print(json.dumps(summary,indent=2))
