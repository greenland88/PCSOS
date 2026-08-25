"""Round 23: cross-year audit of descriptive caution cells."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"
FEATURES = ["nvda_relative_strength20", "nvda_ret20", "nvda_close_vs_sma200",
            "qqq_ret20", "qqq_close_vs_sma50", "nvda_volume_rel20"]

def run():
    d = pd.read_parquet(OUT / "pit_feature_outcome_table.parquet")
    d = d[d.executable_pcs == True].copy()
    rows = []
    for f in FEATURES:
        q = pd.qcut(pd.to_numeric(d[f], errors="coerce"), 5, labels=False, duplicates="drop")
        d["_q"] = q
        # Audit the previously most adverse descriptive quintiles only; no new cutoff search.
        bad = d.assign(_q=q).groupby("_q").apply(lambda g: (g.realized_pnl < 0).mean()).idxmax()
        g0 = d[d._q == bad]
        for year, g in g0.groupby("year"):
            rows.append({"feature": f, "bucket": int(bad) + 1, "year": int(year), "n": len(g),
                         "pnl": float(g.realized_pnl.sum()), "expectancy": float(g.realized_pnl.mean()),
                         "stop_rate": float(g.stopped.mean()), "loss_rate": float((g.realized_pnl < 0).mean())})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "v2_round23_caution_cross_year.csv", index=False)
    stability = out.groupby("feature").agg(years=("year", "nunique"), negative_years=("pnl", lambda s: int((s < 0).sum())), total_n=("n", "sum")).reset_index()
    stability.to_csv(OUT / "v2_round23_caution_stability_summary.csv", index=False)
    result = {"round": 23, "descriptive_only": True, "features_audited": FEATURES,
              "hard_no_trade_added": False, "h010_h027_modified": False,
              "final_oos_touched": False, "production_changed": False,
              "finding": "No caution cell promoted without independent cross-year and setup-specific evidence.",
              "next_action": "MAINTAIN_FROZEN_H010_H027_AND_CONTINUE_INDEPENDENT_EVIDENCE_LEDGER"}
    (OUT / "v2_round23_caution_cross_year.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
