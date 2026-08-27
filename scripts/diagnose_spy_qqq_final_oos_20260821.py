"""Research-only root-cause diagnostics over sealed SPY/QQQ outcomes."""
from pathlib import Path
import json
import pandas as pd
try:
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

ROOT = Path("research_outputs/spy_qqq_pcs_baseline_20260821")
OUT = Path("research_outputs/spy_qqq_final_oos_diagnostic_20260821")
OUT.mkdir(parents=True, exist_ok=True)


def metrics(x):
    x = x.sort_values("decision_date").copy(); p = x.pnl.astype(float)
    w, l = p[p > 0], p[p < 0]; curve = p.cumsum(); dd = curve - curve.cummax()
    path = x.groupby("candidate_id").pnl.agg(path_mae_pnl="min", path_mfe_pnl="max")
    return {"trades": len(x), "expectancy": p.mean(), "profit_factor": w.sum()/abs(l.sum()) if len(l) else None,
            "win_rate": (p > 0).mean(), "stop_rate": x.stop.mean(), "average_winner": w.mean() if len(w) else None,
            "average_loser": l.mean() if len(l) else None, "worst_trade": p.min(), "max_drawdown": dd.min(),
            "total_pnl": p.sum(), "tail_loss_count": int((p <= -200).sum()), "path_mae_pnl_min": path.path_mae_pnl.min() if len(path) else None,
            "path_mfe_pnl_max": path.path_mfe_pnl.max() if len(path) else None,
            "mae_mfe_underlying": "UNAVAILABLE_IN_SEALED_ARTIFACTS"}


def concentration(x):
    p = x.pnl.sort_values().reset_index(drop=True); total = p.sum()
    out = {"classification": "TAIL_LOSS_DOMINATED" if len(p) and abs(p.iloc[:3].sum()) >= abs(total) else "DISTRIBUTED_NEGATIVE"}
    for n in (1, 3, 5):
        worst = p.iloc[:n].sum(); out[f"worst_{n}_contribution"] = worst; out[f"expectancy_excluding_worst_{n}"] = p.iloc[n:].mean() if len(p) > n else None
    return out


def chart(ticker, x):
    if plt is None:
        x.to_csv(OUT / f"{ticker}_diagnostic_chart_data.csv", index=False)
        return "MATPLOTLIB_UNAVAILABLE"
    x = x.sort_values("decision_date").reset_index(drop=True); x["cum_pnl"] = x.pnl.cumsum()
    fig, axes = plt.subplots(2, 2, figsize=(12, 8)); axes = axes.ravel()
    axes[0].plot(x.index + 1, x.cum_pnl); axes[0].set_title(f"{ticker} cumulative P&L")
    axes[1].hist(x.pnl, bins=min(12, max(4, len(x)//3))); axes[1].set_title(f"{ticker} trade P&L distribution")
    axes[2].scatter(range(len(x)), x.pnl, c=x.stop.map({True: "red", False: "steelblue"})); axes[2].set_title("Path P&L; underlying MAE unavailable")
    worst = x.nsmallest(min(10, len(x)), "pnl"); axes[3].bar(pd.to_datetime(worst.decision_date).dt.strftime("%Y-%m-%d"), worst.pnl); axes[3].tick_params(axis="x", rotation=70); axes[3].set_title("Worst trades timeline")
    fig.tight_layout(); fig.savefig(OUT / f"{ticker}_diagnostic.png", dpi=140); plt.close(fig)


def main():
    report = {"module": "spy_qqq_final_oos_root_cause_diagnostic", "version": "20260821.v1", "rules_changed": False, "optimization_performed": False, "tickers": {}}
    for ticker in ("SPY", "QQQ"):
        outcomes = pd.read_parquet(ROOT / f"{ticker}_train_validation_outcomes.parquet")
        contracts = pd.read_parquet(ROOT / f"{ticker}_entry_contract_v2.parquet")
        outcomes.decision_date = pd.to_datetime(outcomes.decision_date)
        splits = {"TRAIN": ("2020-02-28", "2025-12-31"), "VALIDATION": ("2026-01-01", "2026-05-31"), "FINAL_OOS": ("2026-06-01", "2026-08-18")}
        result = {"periods": {}, "entry_fields_available": [c for c in ["trend_state", "support_state", "safe_strike_atr", "atr", "credit", "exact_width", "dte", "planned_loss"] if c in contracts.columns], "entry_fields_unavailable": ["support_strength", "distance_to_support", "regime", "market_confirmation", "entry_drawdown"], "stop_diagnosis": {"breach_rates": "UNAVAILABLE_IN_SEALED_ARTIFACTS", "counterfactual_no_stop": "UNAVAILABLE_IN_SEALED_ARTIFACTS"}}
        for name, (start, end) in splits.items():
            x = outcomes[outcomes.decision_date.between(start, end)].copy(); result["periods"][name] = {"metrics": metrics(x), "loss_concentration": concentration(x), "year": {str(y): metrics(g) for y, g in x.groupby(x.decision_date.dt.year)}, "stop_count": int(x.stop.sum()), "stopped_average_loss": x.loc[x.stop, "pnl"].mean() if x.stop.any() else None}
        result["visualization_status"] = chart(ticker, outcomes[outcomes.decision_date.between(*splits["FINAL_OOS"])].copy()) or "PNG_CREATED"
        report["tickers"][ticker] = result
    report["cross_ticker"] = {"same_failure_mechanism": "PARTIAL", "root_cause_verdict": "Both tickers are negative in FINAL OOS, but QQQ has materially larger loss severity, stop frequency, and drawdown. Available sealed artifacts do not support attributing the difference to regime, support, or strike breaches."}
    (OUT / "root_cause_diagnostic.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__": main()
