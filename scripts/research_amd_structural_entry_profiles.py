"""Research-only AMD structural entry profile analysis."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "amd_structural_entry_profiles_20260820"
OUT.mkdir(parents=True, exist_ok=True)
allx = pd.read_parquet(ROOT / "research_outputs/phase0_20260820/candidate_universe.parquet")
allx = allx[allx.ticker.eq("AMD")].copy(); allx["entry_date"] = pd.to_datetime(allx.decision_date).dt.normalize(); allx["year"] = allx.entry_date.dt.year
rec = pd.read_parquet(ROOT / "research_outputs/amd_stop_outcome_reconstruction_20260820/amd_stopped_trade_reconstruction.parquet")
cols = ["candidate_id","classification","short_strike_touched_after_stop","short_strike_breached_after_stop","stop_outcome","recovered_above_stop_price"]
allx = allx.merge(rec[cols], on="candidate_id", how="left")
allx["structural_failure"] = allx.classification.eq("TRUE_STRUCTURAL_FAILURE")
allx["temporary_stop"] = allx.classification.eq("TEMPORARY_VOLATILITY_STOP")
allx["tail_event"] = allx.classification.eq("TAIL_EVENT")
allx["short_touch"] = allx.short_strike_touched_after_stop.fillna(False); allx["short_breach"] = allx.short_strike_breached_after_stop.fillna(False)
allx["profile"] = np.select([allx.support_state.eq("weak") & allx.trend_state.eq("A"), allx.support_state.eq("weak"), allx.trend_state.eq("A")], ["D_WEAK_SUPPORT_AND_TREND_A","B_WEAK_SUPPORT_ONLY","C_TREND_A_ONLY"], default="A_CLEANER_SUPPORT_NON_A_TREND")

def pf(s):
    pos = s[s > 0].sum(); neg = abs(s[s < 0].sum()); return float(pos / neg) if neg else np.nan
def dd(s): return float((s.cumsum().cummax() - s.cumsum()).max())
def metrics(g):
    return {"trades":len(g),"pnl":float(g.realized_pnl.sum()),"expectancy":float(g.realized_pnl.mean()),"pf":pf(g.realized_pnl),"stop_rate":float(g.exit_reason.eq("STOP").mean()),"strike_touch_rate":float(g.short_touch.mean()),"strike_breach_rate":float(g.short_breach.mean()),"mae":float(g.mae.mean()),"worst_trade":float(g.realized_pnl.min()),"max_drawdown":dd(g.sort_values("entry_date").realized_pnl)}
profile = pd.DataFrame([{**{"profile":k},**metrics(g),"structural_failure_count":int(g.structural_failure.sum()),"temporary_stop_count":int(g.temporary_stop.sum()),"tail_event_count":int(g.tail_event.sum()),"stop_saved_loss_count":int(g.stop_outcome.eq("STOP_SAVED_LOSS").sum()),"stop_avoidable_count":int(g.stop_outcome.eq("STOP_CREATED_AVOIDABLE_LOSS").sum())} for k,g in allx.groupby("profile",sort=True)])
profile.to_csv(OUT/"profile_summary.csv",index=False)
yearly = pd.DataFrame([{**{"profile":k,"year":y},**metrics(g),"structural_failure_rate":float(g.structural_failure.mean())} for (k,y),g in allx.groupby(["profile","year"],sort=True)])
yearly.to_csv(OUT/"profile_yearly_summary.csv",index=False)

def filtered(name, mask):
    g=allx[mask].sort_values("entry_date"); m=metrics(g); collateral=g.theoretical_max_loss.astype(float); years=max((g.entry_date.max()-g.entry_date.min()).days/365.25,1/365.25) if len(g) else np.nan
    m.update({"filter":name,"retained":len(g),"removed":len(allx)-len(g),"annualized_capital_efficiency":float((g.realized_pnl.sum()/collateral.mean())/years) if len(g) and collateral.mean() else np.nan})
    return m
filters = pd.DataFrame([filtered("BASE",allx.index==allx.index),filtered("EXCLUDE_WEAK_SUPPORT",allx.support_state.ne("weak")),filtered("EXCLUDE_TREND_A",allx.trend_state.ne("A")),filtered("EXCLUDE_WEAK_SUPPORT_AND_TREND_A",~(allx.support_state.eq("weak")|allx.trend_state.eq("A"))),filtered("EXCLUDE_ONLY_COMBINED_WEAK_SUPPORT_PLUS_TREND_A",allx.profile.ne("D_WEAK_SUPPORT_AND_TREND_A"))])
base_pnl=float(filters.iloc[0].pnl); filters["loss_avoided_vs_base"] = filters.pnl - base_pnl; filters["profit_sacrificed_vs_base"] = np.maximum(0, base_pnl-filters.pnl); filters.to_csv(OUT/"counterfactual_filters.csv",index=False)
premium = allx.groupby("profile").agg(trades=("candidate_id","size"),credit=("credit","mean"),credit_width=("credit_width_ratio","mean"),credit_per_atr=("credit","mean"),expectancy=("realized_pnl","mean"),stop_rate=("exit_reason",lambda s:s.eq("STOP").mean()),structural_failure_rate=("structural_failure","mean")).reset_index(); premium.to_csv(OUT/"premium_interaction.csv",index=False)
stop = allx[allx.exit_reason.eq("STOP")].groupby("profile").agg(stops=("candidate_id","size"),temporary_rate=("temporary_stop","mean"),recovered_above_stop_rate=("recovered_above_stop_price","mean"),short_touch_rate=("short_touch","mean"),short_breach_rate=("short_breach","mean"),structural_rate=("structural_failure","mean")).reset_index(); stop.to_csv(OUT/"stop_behavior_by_profile.csv",index=False)
summary={"module":"amd_structural_entry_profiles","version":"20260820.v1","symbol":"AMD","as_of":"2026-08-20","status":"COMPLETE","frozen_trades":len(allx),"profile_definitions":"A=strong/moderate support + non-A trend; B=weak only; C=trend A only; D=weak + trend A","capital_efficiency_note":"descriptive pnl divided by mean theoretical_max_loss and elapsed sample years; not a production metric","data_timestamp":"phase0 candidate universe + reconstructed AMD stop paths","calculation_version":"entry-profile-v1","run_id":"amd-structural-entry-profiles-20260820","request_id":"AMD-STRUCTURAL-ENTRY-RESEARCH"}
(OUT/"summary.json").write_text(json.dumps(summary,indent=2,default=str)); print(profile.to_string(index=False)); print(filters.to_string(index=False)); print(stop.to_string(index=False))
