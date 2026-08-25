"""Research-only AMD regime/stop interaction audit; fails closed if regime PIT data is absent."""
from pathlib import Path
import json
import pandas as pd
import numpy as np

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs/amd_regime_stop_interaction_20260820"; OUT.mkdir(parents=True,exist_ok=True)
cu=pd.read_parquet(ROOT/"research_outputs/phase0_20260820/candidate_universe.parquet"); cu=cu[cu.ticker.eq("AMD")].copy(); cu["entry_date"]=pd.to_datetime(cu.decision_date).dt.normalize(); cu["year"]=cu.entry_date.dt.year
rec=pd.read_parquet(ROOT/"research_outputs/amd_stop_outcome_reconstruction_20260820/amd_stopped_trade_reconstruction.parquet")
lm=pd.read_parquet(ROOT/"research_outputs/phase0_20260820/lifecycle_marks.parquet"); lm.mark_date=pd.to_datetime(lm.mark_date).dt.normalize()
st=cu[cu.exit_reason.eq("STOP")][["candidate_id","entry_date","exit_date","credit","realized_pnl"]].merge(rec[["candidate_id","classification","counterfactual_pnl","short_strike_breached_after_stop","long_strike_breached_after_stop"]],on="candidate_id",how="left")
rows=[]
for _,r in st.iterrows():
    m=lm[(lm.candidate_id==r.candidate_id)&(lm.mark_date>=r.entry_date)&(lm.mark_date<r.exit_date)&lm.quote_available&lm.spread_mark.notna()].copy()
    cap=((float(r.credit)-m.spread_mark)/float(r.credit)) if len(m) and r.credit else pd.Series(dtype=float)
    row={"candidate_id":r.candidate_id,"entry_date":r.entry_date,"stop_date":r.exit_date,"regime":"UNAVAILABLE","watermark_data_available":bool(len(m))}
    for t in (40,50,60,70,75): row[f"reached_{t}pct_before_stop"]=bool(len(cap) and (cap>=t/100).any())
    row["max_profit_capture_before_stop_pct"]=float(cap.max()*100) if len(cap) else np.nan; rows.append(row)
wm=pd.DataFrame(rows); wm.to_csv(OUT/"stopped_trade_profit_watermarks.csv",index=False)
wm_summary=pd.DataFrame([{"scope":"ALL_STOPPED","trades":len(wm),**{f"reached_{t}pct_rate":float(wm[f"reached_{t}pct_before_stop"].mean()) for t in (40,50,60,70,75)},"watermark_observable_rate":float(wm.watermark_data_available.mean()),"avg_max_capture_pct":float(wm.max_profit_capture_before_stop_pct.mean())}])
wm_summary.to_csv(OUT/"profit_watermark_summary.csv",index=False)
blocked=pd.DataFrame([{"section":s,"status":"BLOCKED","reason":"AMD frozen population has no persisted MarketRegimeEngine output or complete PIT inputs required by canonical producer; no substitute regime model used."} for s in ["entry_regime","worst_regime_reached","first_defensive_regime_date","regime_at_stop","year_x_regime","stop_behavior_by_regime","cleaner_vs_riskier_within_regime","premium_compensation_by_regime"]]); blocked.to_csv(OUT/"blocked_sections.csv",index=False)
summary={"module":"amd_regime_stop_interaction","version":"20260820.v1","symbol":"AMD","as_of":"2026-08-20","status":"PARTIAL_BLOCKED","regime_sections":"BLOCKED_FAIL_CLOSED","watermark_section":"COMPLETE","frozen_trades":len(cu),"stopped_trades":len(st),"canonical_regime_producer":"pcs.regime.market_regime.MarketRegimeEngine","required_inputs":["qqq_above_20dma","qqq_above_50dma","qqq_above_200dma","spy_above_50dma","soxx_above_50dma","breadth_positive","vix","recent_drawdown_pct/sharp_selloff"],"calculation_version":"regime-stop-interaction-v1","run_id":"amd-regime-stop-interaction-20260820","request_id":"AMD-REGIME-STOP-INTERACTION"}
(OUT/"summary.json").write_text(json.dumps(summary,indent=2,default=str)); print(wm_summary.to_string(index=False)); print(blocked.to_string(index=False))
