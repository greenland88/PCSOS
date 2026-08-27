"""Build final historical comparison from persisted reports only."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs/qqq_frozen_historical_test_2024_2025"
Q = ROOT / "research_outputs/qqq_frozen_validation_20260824"

def main():
    h = json.load(open(OUT / "historical_test_report.json"))
    v = json.load(open(Q / "validation_report.json"))
    hs = json.load(open(Q / "H006_supplemental_validation_report.json"))["result"]
    train = json.load(open(ROOT / "research_outputs/qqq_entry_discovery_agent_v1/artifacts/QQQ_FULL_TRAIN_EVIDENCE_REVIEW.json"))
    cr = json.load(open(ROOT / "research_outputs/qqq_entry_discovery_agent_v1/artifacts/controlled_reset_timing.json"))
    h016 = json.load(open(ROOT / "research_outputs/qqq_entry_discovery_agent_v1/artifacts/h016_sma50_reclaim.json"))
    rows = []
    for name, key in [("Controlled Reset", "Controlled Reset"), ("Recovery Stabilization After Reset", "Recovery Stabilization After Reset"), ("SMA50 Reclaim After Weakness", "SMA50 Reclaim After Weakness")]:
        x = h["strategies"][key]["periods"]
        rows.append({"Strategy": name, "2024 episodes": x["2024"]["independent_episodes"], "2024 trades": x["2024"]["completed_trades"], "2024 P&L": x["2024"]["total_pnl"], "2024 PF": x["2024"]["pf"], "2025 episodes": x["2025"]["independent_episodes"], "2025 trades": x["2025"]["completed_trades"], "2025 P&L": x["2025"]["total_pnl"], "2025 PF": x["2025"]["pf"], "2024-25 total P&L": x["2024-2025"]["total_pnl"], "2024-25 expectancy": x["2024-2025"]["expectancy"], "2024-25 PF": x["2024-2025"]["pf"], "avg holding days": x["2024-2025"]["average_trading_days_held"], "current interpretation": "Descriptive historical diagnostic"})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "qqq_historical_final_comparison.csv", index=False)
    cross = {
        "Controlled Reset": {"2022 TRAIN": cr["timing_rules"]["FIRST_QUALIFICATION"]["results"]["pnl_by_year"]["2022"], "2024": rows[0]["2024 P&L"], "2025": rows[0]["2025 P&L"], "2026 VALIDATION": v["strategies"]["QQQ_CONTROLLED_RESET"]["total_pnl"]},
        "Recovery Stabilization After Reset": {"2022 TRAIN": train["h006_complete_audit"]["year_performance"]["2022"]["total_pnl"], "2024": rows[1]["2024 P&L"], "2025": rows[1]["2025 P&L"], "2026 VALIDATION": hs["total_pnl"]},
        "SMA50 Reclaim After Weakness": {"2022 TRAIN": h016["year_metrics"]["2022"]["pnl"], "2024": rows[2]["2024 P&L"], "2025": rows[2]["2025 P&L"], "2026 VALIDATION": v["strategies"]["QQQ_SMA50_RECLAIM_AFTER_WEAKNESS"]["total_pnl"]},
    }
    interpretation = {"FACT": "2024-2025 combined P&L was positive for all three frozen strategies, but samples were small: 9, 4, and 4 completed trades.", "OBSERVED_PATTERN": "Controlled Reset and Recovery Stabilization were positive in 2024-2025 and negative in 2022 TRAIN / 2026 VALIDATION for the available summaries. SMA50 Reclaim was positive in 2022, negative in 2024, positive in 2025, and positive in 2026 VALIDATION, but remained sparse.", "HYPOTHESIS": "The recovery strategies may be regime-conditional and more vulnerable when recovery is only a bounce inside structural weakness.", "INSUFFICIENT_EVIDENCE": "The samples and LOO tests are too small to establish regime dependence or SMA50 Reclaim superiority. No regime filter is authorized."}
    result = {"comparison": rows, "cross_period_pnl": cross, "interpretation": interpretation, "no_ranking_or_tuning": True, "safety": h["safety"]}
    (OUT / "qqq_historical_final_comparison.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(df.to_string(index=False))
    print(json.dumps(cross, indent=2))

if __name__ == "__main__":
    main()
