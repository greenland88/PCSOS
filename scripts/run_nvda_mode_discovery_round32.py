"""Independent TRAIN robustness screen for the Round 31 NVDA mode leads."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"
SOURCE = OUT / "pit_feature_outcome_table.parquet"

def episodes(x):
    x = x.sort_values("trade_date").copy()
    x["episode_id"] = (x.trade_date.diff().dt.days.fillna(999) > 10).cumsum()
    return x.groupby("episode_id", as_index=False).head(1)

def metric(x):
    x = episodes(x)
    neg = x.loc[x.realized_pnl < 0, "realized_pnl"].sum()
    pos = x.loc[x.realized_pnl > 0, "realized_pnl"].sum()
    return {"episodes": int(len(x)), "pnl": float(x.realized_pnl.sum()),
            "expectancy": float(x.realized_pnl.mean()) if len(x) else None,
            "profit_factor": float(pos / abs(neg)) if neg else None,
            "stop_rate": float(x.stopped.mean()) if len(x) else None,
            "tail_losses": int((x.outcome_class == "TAIL_LOSS").sum()),
            "worst_trade": float(x.realized_pnl.min()) if len(x) else None,
            "years": sorted(int(y) for y in x.year.dropna().unique())}

def run():
    d = pd.read_parquet(SOURCE).copy()
    d.trade_date = pd.to_datetime(d.trade_date).dt.normalize()
    d.date = pd.to_datetime(d.date).dt.normalize()
    d = d[(d.ticker == "NVDA") & d.executable_pcs & d.trade_date.between("2020-01-02", "2023-12-31")].copy()
    median_atr = d.nvda_atr14.median()
    modes = {
        "VOLATILITY_OPPORTUNITY_PCS": (d.nvda_atr14 > median_atr) & (d.nvda_ret5 > 0) & (d.nvda_volume_rel20 > 1),
        "MARKET_CONFIRMED_PCS": (d.qqq_close_vs_sma50 > 0) & (d.nvda_relative_strength20 > 0) & (d.nvda_ret5 > 0),
    }
    rows = []
    details = {}
    for mode, mask in modes.items():
        selected = d[mask]
        rows.append({"mode_id": mode, **metric(selected)})
        year = []
        for y, g in selected.groupby(selected.trade_date.dt.year):
            year.append({"year": int(y), **metric(g)})
        loo = []
        e = episodes(selected)
        for y in sorted(int(y) for y in e.year.dropna().unique()):
            loo.append({"left_out_year": y, **metric(e[e.year != y])})
        ranked = e.sort_values("realized_pnl", ascending=False).reset_index(drop=True)
        top_n = max(1, min(3, len(ranked)))
        concentration = {"episode_count": int(len(e)), "top_1_share": float(ranked.head(1).realized_pnl.sum() / e.realized_pnl.sum()) if e.realized_pnl.sum() else None, "top_3_share": float(ranked.head(top_n).realized_pnl.sum() / e.realized_pnl.sum()) if e.realized_pnl.sum() else None}
        # Structural sensitivity is a predeclared sign/participation sensitivity,
        # not a parameter search or threshold tuning exercise.
        sensitivities = {
            "positive_momentum_any_volume": (d.nvda_ret5 > 0),
            "positive_momentum_and_normal_atr": (d.nvda_ret5 > 0) & (d.nvda_atr14 <= median_atr),
            "positive_momentum_and_elevated_atr": (d.nvda_ret5 > 0) & (d.nvda_atr14 > median_atr),
        }
        sensitivity_rows = [{"sensitivity_id": sid, **metric(d[m])} for sid, m in sensitivities.items()]
        details[mode] = {"year": year, "leave_one_year_out": loo, "pnl_concentration": concentration, "structural_sensitivity": sensitivity_rows}
    pd.DataFrame(rows).to_csv(OUT / "v2_round32_lead_summary.csv", index=False)
    manifest = {"module": "pcs.research.nvda_mode_discovery_round32", "version": "1.0", "symbol": "NVDA", "status": "DESCRIPTIVE_ONLY", "data_source": "PCS_CANONICAL_DATA", "research_mode": "EXISTING_TRADE", "input_rows": int(len(d)), "pit_feature_date_equals_trade_date": bool((d.date == d.trade_date).all()), "validation_read": False, "final_oos_read": False, "production_changes": False, "frozen_rule_families_unchanged": ["PCS_TREND_CONTINUATION", "PCS_CONSTRUCTIVE_RECOVERY"], "leads": details, "reason_codes": ["NVDA_ONLY", "TRAIN_ONLY", "INDEPENDENT_EPISODE_ANALYSIS", "LEAVE_ONE_YEAR_OUT", "PNL_CONCENTRATION", "STRUCTURAL_SENSITIVITY", "NO_VALIDATION", "NO_FINAL_OOS", "NO_PRODUCTION_CHANGE"]}
    (OUT / "v2_round32_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest

if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
