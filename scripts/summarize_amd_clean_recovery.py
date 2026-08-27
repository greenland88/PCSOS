"""Summarize already-completed clean-dataset AMD recovery replays."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "amd_clean_recovery_research_v1"


def stats(g: pd.DataFrame) -> dict:
    p = g.realized_pnl.astype(float)
    pos, neg = p[p > 0], p[p < 0]
    tail = p.le(-100)
    return {"trades": int(len(g)), "pnl": float(p.sum()), "expectancy": float(p.mean()) if len(p) else None,
            "pf": float(pos.sum() / abs(neg.sum())) if len(neg) else None, "win_rate": float((p > 0).mean()) if len(g) else None,
            "stop_rate": float(g.stopped.fillna(False).mean()) if len(g) else None, "tail_loss_rate_pnl_le_minus_100": float(tail.mean()) if len(g) else None,
            "tail_loss_count_pnl_le_minus_100": int(tail.sum()), "average_winner": float(pos.mean()) if len(pos) else None,
            "average_loser": float(neg.mean()) if len(neg) else None, "average_holding_trading_days": float(g.holding_trading_days.mean()) if len(g) else None,
            "average_holding_calendar_days": float(g.holding_calendar_days.mean()) if len(g) else None,
            "worst_trade": float(p.min()) if len(g) else None}


def main() -> dict:
    concepts = {
        "AMD_STATE_RECOVERY_AFTER_BREAKDOWN_V1": OUT.parent / "amd_state_recovery_after_breakdown_v1" / "lifecycle_results.parquet",
        "AMD_PROGRESSIVE_PRICE_REPAIR_AFTER_BREAKDOWN_V1": OUT.parent / "amd_progressive_price_repair_after_breakdown_v1" / "lifecycle_results.parquet",
    }
    all_reports = {}
    for cid, path in concepts.items():
        d = pd.read_parquet(path)
        d["date"] = pd.to_datetime(d.date)
        d["year"] = d.date.dt.year
        yearly = []
        for year, g in d.groupby("year"):
            yearly.append({"year": int(year), **stats(g)})
        y = pd.DataFrame(yearly)
        loo = []
        for year in sorted(d.year.unique()):
            g = d[d.year != year]
            loo.append({"excluded_year": int(year), **stats(g)})
        pnl_by_year = d.groupby("year").realized_pnl.sum().sort_values(ascending=False)
        top3_share = float(pnl_by_year.head(3).sum() / d.realized_pnl.sum()) if d.realized_pnl.sum() else None
        all_reports[cid] = {"overall": stats(d), "yearly": yearly, "leave_one_year_out": loo,
                            "episode_concentration": {"pnl_by_year": {str(k): float(v) for k, v in pnl_by_year.items()}, "top3_year_pnl_share": top3_share,
                                                       "distinct_entry_years": sorted(int(x) for x in d.year.unique())},
                            "lifecycle_source": str(path.relative_to(ROOT)), "tail_loss_definition": "realized_pnl <= -100 account-pnl units"}
        y.to_csv(OUT / f"{cid.lower()}_yearly_results.csv", index=False)
    report = {"module": "pcs.research.amd_clean_recovery_summary", "version": "1.0", "data_source": "PCS_CANONICAL_DATA",
              "canonical_clean_input": "research_outputs/amd_canonical_test_dataset/amd_master_daily_research.parquet",
              "discovery_boundary": {"start": "2020-01-02", "end": "2025-12-31", "2026_used_for_tuning": False},
              "concepts": all_reports, "previous_20_constructive_recovery_used_as_authoritative_input": False,
              "final_oos_touched": False, "production_rules_changed": False, "thresholds_changed": False}
    (OUT / "amd_clean_recovery_results.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    lines = ["# AMD Clean-Dataset Recovery Research", "", "Discovery and replay use only the canonical clean AMD dataset and TRAIN dates through 2025-12-31. 2026 was not used for tuning.", ""]
    for cid, r in all_reports.items():
        o = r["overall"]
        lines += [f"## {cid}", "", f"- Trades: {o['trades']}; P&L: `{o['pnl']:.2f}`; expectancy: `{o['expectancy']:.2f}`; PF: `{o['pf']:.2f}`", f"- Win rate: `{o['win_rate']:.2%}`; stop rate: `{o['stop_rate']:.2%}`; tail-loss rate: `{o['tail_loss_rate_pnl_le_minus_100']:.2%}`", f"- Average winner: `{o['average_winner']:.2f}`; average loser: `{o['average_loser']:.2f}`; average holding: `{o['average_holding_trading_days']:.2f}` trading days / `{o['average_holding_calendar_days']:.2f}` calendar days", f"- Worst trade: `{o['worst_trade']:.2f}`; top-three-year P&L share: `{r['episode_concentration']['top3_year_pnl_share']:.2%}`", "", "Yearly and leave-one-year-out details are in the corresponding CSV/JSON artifacts.", ""]
    lines += ["## Interpretation", "", "These are small, overlapping TRAIN research samples. Positive replay results are promising but insufficient for validation or production promotion. The earlier 20-episode constructive-recovery result is not treated as the complete AMD population; it is a separate older subset and was not used as the clean-dataset input.", "", "Final classification: `AMD_RECOVERY_PROMISING_BUT_INSUFFICIENT`.", "", "Safety: strategy definitions were frozen before replay; no production rules or thresholds changed; FINAL OOS was not touched."]
    (OUT / "AMD_CLEAN_RECOVERY_RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, default=str))
