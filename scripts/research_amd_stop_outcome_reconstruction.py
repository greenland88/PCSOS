"""Read-only ex-post reconstruction of AMD stopped-trade outcomes."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "amd_stop_outcome_reconstruction_20260820"
OUT.mkdir(parents=True, exist_ok=True)
cu = pd.read_parquet(ROOT / "research_outputs/phase0_20260820/candidate_universe.parquet")
lm = pd.read_parquet(ROOT / "research_outputs/phase0_20260820/lifecycle_marks.parquet")
cu = cu[(cu.ticker == "AMD") & (cu.exit_reason == "STOP")].copy()
cu["entry_date"] = pd.to_datetime(cu.decision_date).dt.normalize(); cu["stop_date"] = pd.to_datetime(cu.exit_date).dt.normalize()
lm["mark_date"] = pd.to_datetime(lm.mark_date).dt.normalize()
daily = [pd.read_parquet(p) for p in sorted((ROOT / "data/parquet/daily/symbol=AMD").glob("year=*/AMD_*.parquet"))]
px = pd.concat(daily, ignore_index=True); px["date"] = pd.to_datetime(px.date).dt.normalize(); px = px.sort_values("date").drop_duplicates("date").set_index("date")
rows = []
for _, c in cu.iterrows():
    cid = c.candidate_id; path = px.loc[c.stop_date:c.expiration]; post = path.loc[path.index > c.stop_date]
    entry_close = float(px.loc[c.entry_date, "close"]); stop_close = float(px.loc[c.stop_date, "close"])
    vals = {"candidate_id":cid,"entry_date":c.entry_date,"expiration":c.expiration,"short_strike":c.short_strike,"long_strike":c.long_strike,"initial_credit":c.credit,"stop_date":c.stop_date,"stop_spread_mark":np.nan,"realized_stop_loss":c.realized_pnl,"entry_underlying_price":entry_close,"stop_underlying_price":stop_close,"entry_atr":c.atr}
    m = lm[(lm.candidate_id == cid) & (lm.mark_date == c.stop_date)]
    if len(m): vals["stop_spread_mark"] = float(m.iloc[0].spread_mark)
    for h in (1,2,3,5,10):
        q = post.iloc[:h]; vals[f"price_{h}d"] = float(q.iloc[-1].close) if len(q) else np.nan; vals[f"min_price_{h}d"] = float(q.low.min()) if len(q) else np.nan; vals[f"max_price_{h}d"] = float(q.high.max()) if len(q) else np.nan
    vals["min_price_through_expiration"] = float(post.low.min()) if len(post) else np.nan; vals["max_price_through_expiration"] = float(post.high.max()) if len(post) else np.nan
    vals["post_stop_adverse_move_atr"] = (stop_close - vals["min_price_through_expiration"])/c.atr if len(post) else np.nan; vals["post_stop_recovery_move_atr"] = (vals["max_price_through_expiration"]-stop_close)/c.atr if len(post) else np.nan
    vals["recovered_above_stop_price"] = bool(len(post) and (post.close > stop_close).any()); vals["recovered_above_entry_price"] = bool(len(post) and (post.close > entry_close).any())
    vals["short_strike_touched_after_stop"] = bool(len(post) and (post.low <= c.short_strike).any()); vals["short_strike_breached_after_stop"] = bool(len(post) and (post.close < c.short_strike).any()); vals["long_strike_breached_after_stop"] = bool(len(post) and (post.close < c.long_strike).any())
    vals["time_to_recovery_trading_days"] = next((i+1 for i,x in enumerate(post.close) if x > stop_close), np.nan)
    marks = lm[(lm.candidate_id == cid) & (lm.mark_date >= c.entry_date) & (lm.mark_date <= c.expiration) & lm.quote_available & lm.spread_mark.notna()].sort_values("mark_date"); later = marks[marks.mark_date > c.stop_date]; profit = later[later.spread_mark <= float(c.credit)*0.5]
    if len(profit): cf_mark, cf_reason = float(profit.iloc[0].spread_mark), "PROFIT_CAPTURE"
    elif len(marks): cf_mark, cf_reason = float(marks.iloc[-1].spread_mark), "TIME_EXIT_OR_EXPIRY"
    else: cf_mark, cf_reason = np.nan, "UNRESOLVED"
    vals["counterfactual_exit_reason"] = cf_reason; vals["counterfactual_pnl"] = (float(c.credit)-cf_mark)*100 if pd.notna(cf_mark) else np.nan
    vals["stop_outcome"] = "UNRESOLVED" if pd.isna(vals["counterfactual_pnl"]) else ("STOP_CREATED_AVOIDABLE_LOSS" if vals["counterfactual_pnl"] > c.realized_pnl else ("STOP_SAVED_LOSS" if vals["counterfactual_pnl"] < c.realized_pnl else "STOP_RESULT_SIMILAR"))
    # Descriptive tail label only: top 5% of the reconstructed stopped-trade
    # post-stop adverse-move distribution, calculated after the path pass.
    extreme = False
    vals["classification"] = "TAIL_EVENT" if extreme else ("TEMPORARY_VOLATILITY_STOP" if vals["recovered_above_stop_price"] and not vals["short_strike_breached_after_stop"] else ("TRUE_STRUCTURAL_FAILURE" if vals["short_strike_breached_after_stop"] else "AMBIGUOUS"))
    for f in ("trend_state","support_state","atr_distance","credit_width_ratio","dte","expected_move_1d","price_confirmation","days_to_earnings","short_volume","short_oi"):
        if f in c: vals[f] = c[f]
    rows.append(vals)
out = pd.DataFrame(rows); out.to_parquet(OUT/"amd_stopped_trade_reconstruction.parquet", index=False); out.to_csv(OUT/"amd_stopped_trade_reconstruction.csv", index=False)
tail_cut = out.post_stop_adverse_move_atr.quantile(.95)
out.loc[out.post_stop_adverse_move_atr >= tail_cut, "classification"] = "TAIL_EVENT"
out.to_parquet(OUT/"amd_stopped_trade_reconstruction.parquet", index=False); out.to_csv(OUT/"amd_stopped_trade_reconstruction.csv", index=False)
loss = abs(cu.loc[cu.realized_pnl < 0, "realized_pnl"].sum()); pct = lambda s: float(s.mean()) if len(s) else np.nan
summary = {"module":"amd_stop_outcome_reconstruction","version":"20260820.v1","symbol":"AMD","as_of":"2026-08-20","status":"COMPLETE","trade_count":len(out),"recovered_above_stop_rate":pct(out.recovered_above_stop_price),"recovered_above_entry_rate":pct(out.recovered_above_entry_price),"short_touch_rate":pct(out.short_strike_touched_after_stop),"short_breach_rate":pct(out.short_strike_breached_after_stop),"long_breach_rate":pct(out.long_strike_breached_after_stop),"profitable_counterfactual_rate":pct(out.counterfactual_pnl > 0),"avg_time_to_recovery":float(out.time_to_recovery_trading_days.mean()),"avg_post_stop_adverse_atr":float(out.post_stop_adverse_move_atr.mean()),"avg_counterfactual_pnl":float(out.counterfactual_pnl.mean()),"p90_mae":float(cu.mae.quantile(.90)),"p95_mae":float(cu.mae.quantile(.95)),"p99_mae":float(cu.mae.quantile(.99)),"loss_share_worst_1pct":float(abs(cu.realized_pnl.nsmallest(max(1,int(np.ceil(len(cu)*.01)))).sum())/loss),"loss_share_worst_5pct":float(abs(cu.realized_pnl.nsmallest(max(1,int(np.ceil(len(cu)*.05)))).sum())/loss),"loss_share_worst_10pct":float(abs(cu.realized_pnl.nsmallest(max(1,int(np.ceil(len(cu)*.10)))).sum())/loss),"data_timestamp":"authoritative AMD daily Parquet + phase0 lifecycle marks","calculation_version":"ex-post-no-stop-v1","run_id":"amd-stop-reconstruction-20260820","request_id":"AMD-STOP-OUTCOME-RECONSTRUCTION"}
pd.DataFrame([summary]).to_csv(OUT/"summary.csv", index=False); (OUT/"summary.json").write_text(json.dumps(summary, indent=2, default=str)); print(json.dumps(summary, indent=2, default=str)); print(out.classification.value_counts().to_string()); print(out.stop_outcome.value_counts().to_string())
