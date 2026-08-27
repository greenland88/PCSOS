"""Descriptive, PIT-safe outcome-class comparison for QQQ controlled resets.

This is an EXISTING_TRADE analysis: it freezes the already replayed TRAIN
outcomes, applies the predeclared controlled-reset family, and evaluates one
entry per independent opportunity episode. It does not discover new dates or
change contract/lifecycle parameters.
"""
from pathlib import Path
import json
import pandas as pd

ROOT = Path("research_outputs/qqq_entry_discovery_agent_v1")
ART = ROOT / "artifacts"
FEATURES = [
    "close_sma50_atr", "close_sma200_atr", "ret5", "ret10", "ret20",
    "pullback3", "pullback5", "pullback10", "drawdown60", "atr_pct_rank",
    "vol_pct_rank", "volume_ratio20", "above_sma50", "above_sma200",
]


def independent_entries(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.sort_values("trade_date").copy()
    gap = x.trade_date.diff().dt.days.fillna(999)
    x["episode_id"] = (gap > 4).cumsum()
    return x.groupby("episode_id", as_index=False).first()


def class_summary(frame: pd.DataFrame) -> dict:
    result = {}
    for label, group in frame.groupby("outcome_class", dropna=False):
        result[str(label)] = {
            "episodes": int(len(group)),
            "years": sorted(int(y) for y in group.trade_date.dt.year.unique()),
            "pnl": float(group.realized_pnl.sum()),
            "median_features": {
                feature: (float(group[feature].median()) if len(group) else None)
                for feature in FEATURES
            },
        }
    return result


def run() -> dict:
    data = pd.read_parquet(ART / "qqq_pit_feature_outcome_table_train_2020_2023.parquet")
    data["trade_date"] = pd.to_datetime(data["trade_date"]).dt.normalize()
    family = data[(data["drawdown60"] <= -0.02) & (data["ret10"] > 0)].copy()
    entries = independent_entries(family)
    out = {
        "module": "pcs.research.qqq_controlled_reset_outcome_classes",
        "version": "v1",
        "status": "DESCRIPTIVE_ONLY",
        "data_source": "PCS_CANONICAL_DATA",
        "research_mode": "EXISTING_TRADE",
        "family": "CONTROLLED_RESET",
        "family_rule": "drawdown60 <= -0.02 AND ret10 > 0",
        "qualifying_dates": int(len(family)),
        "independent_episodes": int(len(entries)),
        "episode_pnl": float(entries.realized_pnl.sum()),
        "episode_outcome_counts": entries.outcome_class.value_counts().to_dict(),
        "by_outcome_class": class_summary(entries),
        "by_year": {
            str(year): {
                "episodes": int(len(group)),
                "pnl": float(group.realized_pnl.sum()),
                "outcomes": group.outcome_class.value_counts().to_dict(),
            }
            for year, group in entries.groupby(entries.trade_date.dt.year)
        },
        "threshold_mining": False,
        "validation_read": False,
        "final_oos_read": False,
        "production_changes": False,
        "reason_codes": ["PIT_SAFE_FEATURES", "ONE_ENTRY_PER_EPISODE", "DESCRIPTIVE_ONLY"],
    }
    (ART / "controlled_reset_outcome_classes.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    entries.to_csv(ART / "controlled_reset_independent_episode_ledger.csv", index=False)
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
