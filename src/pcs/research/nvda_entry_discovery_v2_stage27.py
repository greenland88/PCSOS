"""Round 27: setup-specific caution interaction audit for frozen H010/H027."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"

def episode_rows(d):
    d = d[d.executable_pcs == True].sort_values("trade_date").copy()
    d["trade_date"] = pd.to_datetime(d.trade_date)
    d["episode"] = (d.trade_date.diff().dt.days.fillna(999) > 10).cumsum()
    return d.groupby("episode", as_index=False).head(1).copy()

def metrics(g):
    pos = g.loc[g.realized_pnl > 0, "realized_pnl"].sum()
    neg = g.loc[g.realized_pnl < 0, "realized_pnl"].sum()
    return {"episodes": int(len(g)), "pnl": float(g.realized_pnl.sum()),
            "expectancy": float(g.realized_pnl.mean()) if len(g) else None,
            "pf": float(pos / abs(neg)) if neg else None,
            "stop_rate": float(g.stopped.mean()) if len(g) else None,
            "worst_trade": float(g.realized_pnl.min()) if len(g) else None}

def run():
    d = pd.read_parquet(OUT / "pit_feature_outcome_table.parquet")
    d["low_participation"] = d.nvda_volume_rel20 < 1
    d["qqq_weak"] = d.qqq_close_vs_sma50 < 0
    d["long_term_weak"] = d.nvda_close_vs_sma200 < 0
    families = {
        "PCS_TREND_CONTINUATION": (d.nvda_close_vs_sma200 > 0) & (d.nvda_volume_rel20 > 1) & (d.nvda_ret5 > 0),
        "PCS_CONSTRUCTIVE_RECOVERY": (d.nvda_close_vs_sma200 > 0) & (d.nvda_ret20 < 0) & (d.nvda_ret5 > 0),
    }
    states = {"LOW_PARTICIPATION": d.low_participation,
              "LOW_PARTICIPATION_QQQ_WEAK": d.low_participation & d.qqq_weak,
              "QQQ_NVDA_LONG_TERM_WEAK": d.qqq_weak & d.long_term_weak}
    rows = []
    for family, mask in families.items():
        base = episode_rows(d[mask])
        for state, smask in states.items():
            inside = base[base.trade_date.isin(d.loc[smask, "trade_date"])]
            outside = base[~base.trade_date.isin(inside.trade_date)]
            for label, g in [("inside", inside), ("outside", outside)]:
                rows.append({"family": family, "state": state, "side": label, **metrics(g)})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "v2_round27_setup_specific_caution_interactions.csv", index=False)
    result = {"round": 27, "families": list(families), "states": list(states),
              "descriptive_only": True, "hard_no_trade_added": False,
              "h010_h027_modified": False, "final_oos_touched": False,
              "production_changed": False,
              "next_action": "REVIEW_SETUP_SPECIFIC_CAUTION_INTERACTIONS_AND_PRESERVE_ONLY_EVIDENCE"}
    (OUT / "v2_round27_setup_specific_caution.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
