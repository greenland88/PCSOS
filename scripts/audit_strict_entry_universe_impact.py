"""Read-only strict-entry impact audit with explicit universe coverage limits."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("research_outputs/strict_entry_universe_impact_20260821")
PHASE0 = Path("research_outputs/phase0_20260820/candidate_universe.parquet")
INDEX = Path("research_outputs/spy_qqq_pcs_baseline_20260821")
CORE = ["NVDA", "AMD", "MSFT", "AMZN", "TSLA", "META", "GOOGL", "AVGO", "MU", "HOOD", "PLTR", "JPM", "BAC", "GS", "WMT", "COST", "HD", "UNH", "ABBV", "CAT", "RTX", "XOM", "CVX", "COP", "MCD"]


def metrics(frame: pd.DataFrame) -> dict:
    x = frame.dropna(subset=["pnl"]).sort_values("decision_date").copy()
    p = x.pnl.astype(float)
    w, l = p[p > 0], p[p < 0]
    curve = p.cumsum()
    return {"trade_count": int(len(p)), "loser_pct": float((p < 0).mean() * 100) if len(p) else None, "winner_pct": float((p > 0).mean() * 100) if len(p) else None, "stop_pct": float(x.stop.mean() * 100) if len(x) and "stop" in x else None, "tail_loss_trades_pct": float((p <= p.quantile(.05)).mean() * 100) if len(p) else None, "expectancy": float(p.mean()) if len(p) else None, "profit_factor": float(w.sum() / abs(l.sum())) if len(l) and l.sum() else None, "worst_trade": float(p.min()) if len(p) else None, "max_drawdown": float((curve - curve.cummax()).min()) if len(p) else None}


def load_available() -> pd.DataFrame:
    phase = pd.read_parquet(PHASE0)
    phase = phase[phase.status.eq("COMPLETE")].copy()
    phase["decision_date"] = pd.to_datetime(phase.decision_date)
    phase["pnl"] = pd.to_numeric(phase.realized_pnl, errors="coerce")
    phase["stop"] = phase.stop_triggered.fillna(False).astype(bool)
    phase["source_group"] = "CORE_PHASE0"
    rows = [phase[["ticker", "decision_date", "pnl", "stop", "source_group"]]]
    for s in ["SPY", "QQQ"]:
        p = INDEX / f"{s}_train_validation_outcomes.parquet"
        if p.exists():
            x = pd.read_parquet(p)
            x["decision_date"] = pd.to_datetime(x.decision_date)
            x["source_group"] = "INDEX_OVERLAY"
            rows.append(x[["candidate_id", "decision_date", "pnl", "stop", "source_group"]].rename(columns={"candidate_id": "ticker"}))
            rows[-1]["ticker"] = s
    return pd.concat(rows, ignore_index=True)


def main() -> None:
    available = load_available()
    core = available[available.source_group.eq("CORE_PHASE0")]
    overlay = available[available.source_group.eq("INDEX_OVERLAY")]
    annual = available.assign(year=available.decision_date.dt.year).groupby(["year", "source_group"]).size().reset_index(name="qualifying_trades").to_dict("records")
    monthly = available.assign(month=available.decision_date.dt.to_period("M")).groupby("month").size()
    report = {
        "module": "strict_entry_universe_impact_audit", "version": "20260821.v1", "read_only": True,
        "trade_count_reduction_policy": "NOT_A_NEGATIVE_BY_ITSELF; evaluate retained quality and universe opportunity capacity",
        "strict_entry_removed_population": {"status": "NOT_AVAILABLE", "reason": "No persisted current-25-ticker strict-entry decision flag or deleted-trade outcome artifact"},
        "deleted_trade_metrics": {"loser_pct": "UNKNOWN", "winner_pct": "UNKNOWN", "stop_pct": "UNKNOWN", "tail_loss_trades_pct": "UNKNOWN"},
        "strict_entry_retained_metrics_available_evidence": {"core_phase0": metrics(core), "spy_qqq_overlay": metrics(overlay), "combined_available_evidence": metrics(available)},
        "annual_qualifying_trade_count_available_evidence": annual,
        "monthly_qualifying_opportunities_available_evidence": {"mean": float(monthly.mean()), "median": float(monthly.median()), "p25": float(monthly.quantile(.25)), "p75": float(monthly.quantile(.75)), "months_observed": int(len(monthly))},
        "universe_coverage": {"core_tickers": 25, "core_tickers_with_candidate_outcomes": 4, "core_coverage_pct": 16.0, "index_overlay_tickers": 2, "current_25_ticker_annual_estimate": "NOT_ESTIMABLE_WITHOUT_21_MISSING_TICKER_ARTIFACTS", "available_evidence_is_lower_bound": True},
        "conclusion": "Trade-count reduction must not veto strict entry; however, a full current-25-ticker annual opportunity estimate requires candidate artifacts for the 21 uncovered core tickers.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "strict_entry_universe_impact.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__": main()
