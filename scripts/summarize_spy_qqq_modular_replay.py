"""Research-only policy and capital-efficiency summaries.

Consumes the bounded monthly replay outputs.  It never loads options data and
never accesses FINAL OOS; validation rows already marked boundary-blocked stay
unobservable in every P&L statistic.
"""
from __future__ import annotations

from pathlib import Path
import json
import math

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "spy_qqq_modular_rule_research_20260821"
POLICIES = ("UNCAPPED_BASELINE", "ELIGIBILITY_RESET_ONLY", "SETUP_ONE", "SETUP_ONE_PLUS_ONE_SCALE", "MAX1", "MAX2")


def setupize(trades: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Assign deterministic eligibility-reset setups using actual daily rows.

    A gap in *available trading dates* breaks eligibility.  This avoids treating
    weekends/holidays as resets and does not use expiration or strike changes.
    """
    entries = trades.copy()
    entries["date"] = pd.to_datetime(entries["date"]).dt.normalize()
    entries["exit_date"] = pd.to_datetime(entries["exit_date"]).dt.normalize()
    entries["setup_id"] = None
    entries["setup_start_reason"] = None
    entries["scale_in_order"] = 0
    for ticker, block in entries.groupby("ticker", sort=True):
        calendar = pd.to_datetime(daily.loc[daily.ticker.eq(ticker), "date"]).drop_duplicates().sort_values().tolist()
        pos = {d: i for i, d in enumerate(calendar)}
        prior_i = None
        seq = 0
        setup = None
        order = 0
        for idx, row in block.sort_values(["date", "candidate_id"], kind="stable").iterrows():
            current_i = pos[row.date]
            # Any intervening available date without a selected candidate resets.
            continuous = prior_i is not None and current_i == prior_i + 1
            if not continuous:
                seq += 1
                setup = f"{ticker}-{row.date:%Y%m%d}-{seq:03d}"
                order = 1
                reason = "INITIAL_OBSERVED_ELIGIBLE" if prior_i is None else "ELIGIBILITY_FALSE_TO_TRUE"
            else:
                order += 1
                reason = "ACTIVE_SETUP"
            entries.loc[idx, ["setup_id", "setup_start_reason", "scale_in_order"]] = [setup, reason, order]
            prior_i = current_i
    return entries


def select_policy(frame: pd.DataFrame, policy: str, combined: bool) -> pd.DataFrame:
    x = frame.sort_values(["date", "ticker", "candidate_id"], kind="stable").copy()
    if policy == "UNCAPPED_BASELINE":
        return x
    if policy == "ELIGIBILITY_RESET_ONLY" or policy == "SETUP_ONE":
        return x[x.scale_in_order.eq(1)].copy()
    if policy == "SETUP_ONE_PLUS_ONE_SCALE":
        return x[x.scale_in_order.le(2)].copy()
    cap = 1 if policy == "MAX1" else 2
    result = []
    active: list[dict] = []
    for _, row in x.iterrows():
        # Exit date is inclusive for lifecycle exposure; release only before a
        # later calendar day. Unknown/boundary-blocked exits remain active.
        active = [p for p in active if pd.isna(p["exit_date"]) or p["exit_date"] >= row.date]
        scope = active if combined else [p for p in active if p["ticker"] == row.ticker]
        if len(scope) < cap:
            rec = row.to_dict(); result.append(rec); active.append(rec)
    return pd.DataFrame(result, columns=x.columns)


def exposures(x: pd.DataFrame, daily: pd.DataFrame) -> tuple[int, float, float]:
    if x.empty:
        return 0, math.nan, math.nan
    dates = pd.to_datetime(daily.date).drop_duplicates().sort_values()
    counts = []
    for date in dates:
        n = ((x.date <= date) & (x.exit_date.isna() | (x.exit_date >= date))).sum()
        counts.append(int(n))
    return max(counts), float(sum(counts) / len(counts)), float(x.planned_loss.sum() if len(x) else math.nan)


def metrics(x: pd.DataFrame, daily: pd.DataFrame, label: str, scope: str, policy: str) -> dict:
    observed = x[x.pnl.notna()].copy()
    pnl = observed.pnl.astype(float)
    wins = pnl[pnl > 0]; losses = pnl[pnl < 0]
    max_con, avg_con, peak_planned = exposures(x, daily)
    if observed.empty:
        maxdd = worst = total = pf = expectancy = winrate = math.nan
    else:
        curve = observed.assign(exit_sort=observed.exit_date.fillna(observed.date)).groupby("exit_sort", sort=True).pnl.sum().cumsum()
        maxdd = float((curve.cummax() - curve).max())
        total = float(pnl.sum()); worst = float(pnl.min()); expectancy = float(pnl.mean())
        pf = float(wins.sum() / abs(losses.sum())) if not losses.empty else math.inf
        winrate = float((pnl > 0).mean())
    return {"split": label, "scope": scope, "policy": policy, "entries": len(x), "observed_entries": len(observed),
            "final_oos_boundary_blocked": int(x.exit_reason.eq("FINAL_OOS_BOUNDARY_BLOCKED").sum()),
            "independent_setups": int(x.setup_id.nunique()), "entries_per_setup": float(len(x) / x.setup_id.nunique()) if x.setup_id.nunique() else math.nan,
            "total_pnl": total, "expectancy": expectancy, "profit_factor": pf, "win_rate": winrate,
            "max_drawdown": maxdd, "worst_trade": worst, "stop_rate": float(x.stop.mean()) if len(x) else math.nan,
            "peak_planned_loss": peak_planned, "pnl_to_peak_planned_loss": total / peak_planned if peak_planned and not pd.isna(total) else math.nan,
            "max_concurrent_positions": max_con, "avg_concurrent_positions": avg_con,
            "simultaneous_stops": int(x[x.stop].groupby("exit_date").size().ge(2).sum()) if len(x) else 0}


def main() -> None:
    all_metrics=[]; all_entries=[]; all_setups=[]; annual=[]; concurrency=[]; scale=[]
    for label in ("train", "validation"):
        trades_path=OUT/f"{label}_selected_lifecycle.parquet"; daily_path=OUT/f"{label}_daily_decision_ledger.parquet"
        trades=pd.read_parquet(trades_path); daily=pd.read_parquet(daily_path)
        x=setupize(trades,daily)
        all_entries.append(x.assign(split=label))
        all_setups.append(x.groupby(["ticker","setup_id"],as_index=False).agg(setup_start=("date","min"),setup_end=("date","max"),eligible_entry_count=("candidate_id","size"),baseline_entry_count=("candidate_id","size"),portfolio_rejection_count=("candidate_id",lambda _:0),first_reason=("setup_start_reason","first"),unresolved=("exit_reason",lambda z:(z=="FINAL_OOS_BOUNDARY_BLOCKED").any())).assign(split=label))
        for policy in POLICIES:
            for ticker in ("SPY","QQQ"):
                p=select_policy(x[x.ticker.eq(ticker)],policy,False)
                d=daily[daily.ticker.eq(ticker)]
                all_metrics.append(metrics(p,d,label,ticker,policy)); concurrency.append({**metrics(p,d,label,ticker,policy),"same_day_correlated_loss":float(p[p.pnl.notna()].groupby("exit_date").pnl.sum().min()) if not p[p.pnl.notna()].empty else math.nan})
                for year, g in p.groupby(p.date.dt.year): annual.append({**metrics(g,d[d.date.dt.year.eq(year)],label,ticker,policy),"year":year})
            p=select_policy(x,policy,True)
            all_metrics.append(metrics(p,daily,label,"SPY+QQQ",policy))
        for order,g in x.groupby("scale_in_order"):
            all = {"split":label,"scale_in_order":int(order),"entries":len(g),"observed_entries":int(g.pnl.notna().sum()),"total_pnl":float(g.pnl.sum()) if g.pnl.notna().any() else math.nan,"expectancy":float(g.pnl.mean()) if g.pnl.notna().any() else math.nan}
            scale.append(all)
    entries=pd.concat(all_entries,ignore_index=True); setups=pd.concat(all_setups,ignore_index=True)
    entries.to_parquet(OUT/"policy_entries.parquet",index=False); setups.to_csv(OUT/"opportunity_setups.csv",index=False)
    pd.DataFrame(all_metrics).to_csv(OUT/"scenario_metrics.csv",index=False);pd.DataFrame(annual).to_csv(OUT/"annual_policy_comparison.csv",index=False)
    pd.DataFrame(concurrency).to_csv(OUT/"portfolio_concurrency_analysis.csv",index=False);pd.DataFrame(scale).to_csv(OUT/"scale_in_marginal_analysis.csv",index=False)
    # Rule contribution is coverage, not a causal claim: unknown gates cannot be
    # assigned a performance contribution.
    rows=[]
    for label in ("train","validation"):
        c=pd.read_parquet(OUT/f"{label}_candidate_gate_ledger.parquet"); c=c[c["mode"].eq("FULL_AUDIT")]
        for rule in [z for z in c.columns if z in {"dte_range","safe_strike_atr","credit_efficiency","spread_width","quote_validity","liquidity_gate","planned_loss","trend_gate","support_gate","event_gate","regime_gate"}]:
            for state,n in c[rule].value_counts(dropna=False).items(): rows.append({"split":label,"rule_id":rule,"status":state,"candidate_rows":n,"newly_admitted": "NOT_COMPUTABLE","still_rejected_elsewhere":"NOT_COMPUTABLE"})
    pd.DataFrame(rows).to_csv(OUT/"rule_marginal_contribution.csv",index=False)
    # The event/regime adapters are genuinely UNKNOWN in the preserved historical
    # context.  Disabling an UNKNOWN adapter cannot be represented as a measured
    # performance improvement, so ablations are explicitly non-computable rather
    # than duplicated as if they were independent reruns.
    base=pd.DataFrame(all_metrics)
    comparison=[]
    for scenario, status, reason in [
        ("RESEARCH_CURRENT_RULES_AVAILABLE_CONTEXT","COMPUTED","AVAILABLE_CONTEXT_CHAIN"),
        ("RESEARCH_CURRENT_RULES_EVENT_DISABLED","NOT_COMPUTABLE","EVENT_CALENDAR_UNAVAILABLE"),
        ("RESEARCH_CURRENT_RULES_REGIME_DISABLED","NOT_COMPUTABLE","MARKET_STATE_UNAVAILABLE"),
        ("CURRENT_ENTRY_V1_STRICT","NOT_COMPUTABLE","REQUIRED_HISTORICAL_CONTEXT_UNAVAILABLE"),
    ]:
        y=base.copy(); y["scenario_id"]=scenario; y["scenario_status"]=status; y["scenario_reason"]=reason
        comparison.append(y)
    pd.concat(comparison,ignore_index=True).to_csv(OUT/"scenario_comparison.csv",index=False)
    (OUT/"research_summary.md").write_text("# Modular research replay\n\nStatus: COMPLETED_WITH_RULE_LEVEL_UNKNOWNS.  Results are research-only. Unknown context gates are reported as UNKNOWN and have no implied PASS/FAIL contribution. Validation lifecycle rows requiring marks after 2026-05-31 are FINAL_OOS_BOUNDARY_BLOCKED and excluded from P&L.\n\nPRODUCTION RULE CHANGED: NO\nPRODUCTION LOGIC CHANGED: NO\nPRODUCTION CONFIG CHANGED: NO\nFROZEN ARTIFACTS CHANGED: NO\nFINAL OOS READ: NO\nVALIDATION USED FOR TUNING: NO\nRESEARCH ONLY: YES\n",encoding="utf8")
    print(json.dumps({"entries":len(entries),"setups":len(setups),"metrics":len(all_metrics)},indent=2))

if __name__=="__main__": main()
