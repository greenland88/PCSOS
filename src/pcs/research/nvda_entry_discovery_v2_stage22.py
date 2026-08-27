"""Round 22: broad, fixed-quantile PIT caution-state diagnostics.

Quantiles are descriptive diagnostics only; this module does not emit or add
an entry/no-trade rule.
"""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"
FEATURES = ["nvda_atr14", "nvda_ret5", "nvda_ret20", "nvda_drawdown20",
            "nvda_volume_rel20", "qqq_ret5", "qqq_ret20",
            "nvda_close_vs_sma20", "nvda_close_vs_sma50", "nvda_close_vs_sma200",
            "qqq_close_vs_sma50", "nvda_relative_strength20", "consecutive_down_days"]

def run():
    d = pd.read_parquet(OUT / "pit_feature_outcome_table.parquet")
    d = d[d.executable_pcs == True].copy()
    rows = []
    for f in FEATURES:
        s = pd.to_numeric(d[f], errors="coerce")
        # Fixed descriptive quintiles over TRAIN only; no threshold is promoted.
        q = pd.qcut(s, 5, labels=False, duplicates="drop")
        for bucket, g in d.assign(_bucket=q).groupby("_bucket", dropna=True):
            rows.append({"feature": f, "bucket": int(bucket) + 1, "n": len(g),
                         "years": sorted(g.year.astype(int).unique().tolist()),
                         "pnl": float(g.realized_pnl.sum()), "expectancy": float(g.realized_pnl.mean()),
                         "stop_rate": float(g.stopped.mean()),
                         "loss_rate": float((g.realized_pnl < 0).mean()),
                         "tail_loss_count": int((g.outcome_class == "TAIL_LOSS").sum())})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "v2_round22_fixed_quantile_caution_profiles.csv", index=False)
    # Report only cells with meaningful sample; this remains descriptive.
    ranked = out[out.n >= 20].sort_values(["stop_rate", "loss_rate"], ascending=False).head(20)
    ranked.to_csv(OUT / "v2_round22_high_risk_descriptive_cells.csv", index=False)
    result = {"round": 22, "executable": int(len(d)), "features": FEATURES,
              "quantiles": 5, "descriptive_only": True, "hard_no_trade_added": False,
              "h010_h027_modified": False, "final_oos_touched": False,
              "production_changed": False, "next_action": "REVIEW_QUANTILE_CELLS_FOR_CROSS_YEAR_CAUTION_STABILITY"}
    (OUT / "v2_round22_caution_profile.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
