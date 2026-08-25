"""Round 21: descriptive, non-threshold-mined PIT bad-state audit."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"

def run() -> dict:
    df = pd.read_parquet(OUT / "pit_feature_outcome_table.parquet")
    pit = ["nvda_atr14", "nvda_sma20", "nvda_sma50", "nvda_sma200", "nvda_ret5",
           "nvda_ret10", "nvda_ret20", "nvda_drawdown20", "nvda_volume_rel20",
           "qqq_ret5", "qqq_ret20", "nvda_close_vs_sma20", "nvda_close_vs_sma50",
           "nvda_close_vs_sma200", "qqq_close_vs_sma50", "nvda_relative_strength20",
           "consecutive_down_days"]
    rows = []
    for outcome, g in df[df.executable_pcs == True].groupby("outcome_class", dropna=False):
        for feature in pit:
            s = pd.to_numeric(g[feature], errors="coerce").dropna()
            if len(s):
                rows.append({"outcome_class": outcome, "feature": feature, "n": len(s),
                             "mean": float(s.mean()), "median": float(s.median()),
                             "p25": float(s.quantile(.25)), "p75": float(s.quantile(.75)),
                             "stop_rate": float(g.stopped.fillna(False).mean()),
                             "loss_rate": float((g.realized_pnl < 0).mean()),
                             "pnl": float(g.realized_pnl.sum())})
    prof = pd.DataFrame(rows)
    prof.to_csv(OUT / "v2_round21_bad_state_feature_profiles.csv", index=False)
    summary = (df[df.executable_pcs == True].groupby(["year", "outcome_class"])
               .agg(n=("trade_date", "size"), pnl=("realized_pnl", "sum"),
                    stop_rate=("stopped", "mean"), loss_rate=("realized_pnl", lambda s: float((s < 0).mean())))
               .reset_index())
    summary.to_csv(OUT / "v2_round21_bad_state_yearly.csv", index=False)
    result = {"round": 21, "rows": int(len(df)), "executable": int(df.executable_pcs.sum()),
              "outcome_classes": df[df.executable_pcs == True].outcome_class.value_counts(dropna=False).to_dict(),
              "pit_features_profiled": pit, "hard_no_trade_added": False,
              "h010_h027_modified": False, "final_oos_touched": False, "production_changed": False}
    (OUT / "v2_round21_bad_state_audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
