"""Round 28: PIT qualifying-date independence matrix for existing families."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"

def run():
    d = pd.read_parquet(OUT / "pit_feature_outcome_table.parquet")
    d = d[d.executable_pcs == True].copy()
    atr_med = d.nvda_atr14.median()
    masks = {
        "H010_TREND": (d.nvda_close_vs_sma200 > 0) & (d.nvda_volume_rel20 > 1) & (d.nvda_ret5 > 0),
        "H027_RECOVERY": (d.nvda_close_vs_sma200 > 0) & (d.nvda_ret20 < 0) & (d.nvda_ret5 > 0),
        "H015_VOL_EXPANSION": (d.nvda_atr14 > atr_med) & (d.nvda_volume_rel20 > 1) & (d.nvda_ret5 > 0),
        "H016_MARKET_CONTEXT": (d.qqq_close_vs_sma50 > 0) & (d.nvda_relative_strength20 >= 0) & (d.nvda_ret5 > 0),
        "H021_BALANCED_RECOVERY": (d.nvda_ret20 < 0) & (d.nvda_ret5 > 0) & (d.nvda_volume_rel20 > 1) & (d.nvda_atr14 <= atr_med),
        "H022_MARKET_PARTICIPATION": (d.nvda_close_vs_sma200 > 0) & (d.qqq_close_vs_sma50 > 0) & (d.nvda_volume_rel20 > 1) & (d.nvda_ret5 > 0),
        "H023_RELATIVE_STRENGTH": (d.nvda_close_vs_sma200 > 0) & (d.nvda_relative_strength20 >= 0) & (d.nvda_volume_rel20 > 1) & (d.nvda_ret5 > 0),
        "H028_RANGE": (d.nvda_close_vs_sma20 > 0) & (d.nvda_close_vs_sma50 > 0) & (d.nvda_ret5 > 0),
    }
    sets = {k: set(d.index[m]) for k, m in masks.items()}
    rows = []
    for a, sa in sets.items():
        for b, sb in sets.items():
            if a < b:
                union = sa | sb
                rows.append({"family_a": a, "family_b": b, "a_dates": len(sa), "b_dates": len(sb), "intersection": len(sa & sb), "jaccard": len(sa & sb) / len(union) if union else None, "a_contained_in_b": sa <= sb, "b_contained_in_a": sb <= sa})
    pd.DataFrame(rows).to_csv(OUT / "v2_round28_family_independence_matrix.csv", index=False)
    result = {"round": 28, "families": list(masks), "definition_changes": False,
              "h010_h027_modified": False, "final_oos_touched": False, "production_changed": False,
              "next_action": "USE_INDEPENDENCE_MATRIX_TO_SELECT_ONLY_GENUINELY_DISTINCT_RESEARCH_PATHS"}
    (OUT / "v2_round28_family_independence.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
