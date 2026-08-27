"""Individual PIT-safe audit of controlled-reset loss episodes."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path("research_outputs/qqq_entry_discovery_agent_v1")
ART = ROOT / "artifacts"
FEATURES = [
    "close_sma50_atr", "close_sma200_atr", "ret5", "ret10", "ret20",
    "pullback3", "pullback5", "pullback10", "drawdown60", "atr_pct_rank",
    "vol_pct_rank", "volume_ratio20", "above_sma50", "above_sma200",
    "TREND_WEAKENING", "VOLATILITY_EXPANDING", "DRAWDOWN_DEEPENING",
    "RECOVERY_AFTER_RESET",
]


def run():
    d = pd.read_parquet(ART / "qqq_state_transition_features_train_2020_2023.parquet")
    d.trade_date = pd.to_datetime(d.trade_date).dt.normalize()
    family = d[(d.drawdown60 <= -0.02) & (d.ret10 > 0)].copy()
    family["episode_id"] = (family.trade_date.diff().dt.days.fillna(999) > 4).cumsum()
    entries = family.groupby("episode_id", as_index=False).first()
    losses = entries[entries.outcome_class.isin(["STOP_LOSS", "TAIL_LOSS"])].copy()
    loss_rows = []
    for _, row in losses.iterrows():
        loss_rows.append({"trade_date": str(row.trade_date.date()), "year": int(row.trade_date.year),
                          "outcome_class": row.outcome_class, "realized_pnl": float(row.realized_pnl),
                          **{f: (bool(row[f]) if f.startswith(("above_", "TREND_", "VOLATILITY_", "DRAWDOWN_", "RECOVERY_")) else float(row[f])) for f in FEATURES}})
    summary = {"loss_episodes": int(len(losses)), "stops": int((losses.outcome_class == "STOP_LOSS").sum()),
               "tails": int((losses.outcome_class == "TAIL_LOSS").sum()),
               "years": {str(y): int((losses.trade_date.dt.year == y).sum()) for y in sorted(losses.trade_date.dt.year.unique())},
               "state_true_counts": {f: int(losses[f].fillna(False).astype(bool).sum()) for f in FEATURES if f.startswith(("above_", "TREND_", "VOLATILITY_", "DRAWDOWN_", "RECOVERY_"))},
               "feature_medians": {f: float(losses[f].median()) for f in FEATURES if not f.startswith(("above_", "TREND_", "VOLATILITY_", "DRAWDOWN_", "RECOVERY_"))}}
    out = {"module": "pcs.research.qqq_controlled_reset_loss_date_audit", "version": "v1",
           "status": "DESCRIPTIVE_ONLY", "data_source": "PCS_CANONICAL_DATA", "research_mode": "EXISTING_TRADE",
           "family_rule": "drawdown60 <= -0.02 AND ret10 > 0", "independent_episode_count": int(len(entries)),
           "summary": summary, "loss_episodes": loss_rows, "threshold_mining": False,
           "validation_read": False, "final_oos_read": False, "production_changes": False,
           "reason_codes": ["PIT_SAFE_FEATURES", "ONE_ENTRY_PER_EPISODE", "INDIVIDUAL_LOSS_AUDIT", "DESCRIPTIVE_ONLY"]}
    (ART / "controlled_reset_loss_date_audit.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))
    return out


if __name__ == "__main__":
    run()
