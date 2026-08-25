"""Four-year descriptive evaluation of the frozen H005 family."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path("research_outputs/qqq_entry_discovery_agent_v1")
ART = ROOT / "artifacts"


def metric(g):
    p = g.realized_pnl; wins = p[p > 0]; losses = p[p < 0]
    return {"dates": int(len(g)), "pnl": float(p.sum()),
            "pf": float(wins.sum() / abs(losses.sum())) if len(losses) else None,
            "good_wins": int((g.outcome_class == "GOOD_WIN").sum()),
            "normal_losses": int((g.outcome_class == "NORMAL_LOSS").sum()),
            "stops": int((g.outcome_class == "STOP_LOSS").sum()),
            "tails": int((g.outcome_class == "TAIL_LOSS").sum()),
            "years": sorted(int(y) for y in g.trade_date.dt.year.unique())}


def run():
    d = pd.read_parquet(ART / "qqq_pit_feature_outcome_table_train_2020_2023.parquet")
    d.trade_date = pd.to_datetime(d.trade_date).dt.normalize()
    # Frozen from the prior TRAIN descriptive screen; no new thresholds.
    family = d[d.close_sma200_atr.between(0.0879, 8.109, inclusive="right") & d.vol_pct_rank.between(0.429, 0.753, inclusive="right")].copy()
    family["episode_id"] = (family.trade_date.diff().dt.days.fillna(999) > 4).cumsum()
    entries = family.groupby("episode_id", as_index=False).first()
    out = {"module":"pcs.research.qqq_moderate_vol_trend_family_eval", "version":"v1", "status":"DESCRIPTIVE_ONLY", "data_source":"PCS_CANONICAL_DATA", "research_mode":"EXISTING_TRADE", "family":"H005_TREND_CONFIRMED_MODERATE_VOLATILITY", "frozen_rule":"0.0879 < close_sma200_atr <= 8.109 AND 0.429 < vol_pct_rank <= 0.753", "qualifying_dates":int(len(family)), "independent_episodes":int(len(entries)), "all_dates":metric(family), "one_entry_per_episode":metric(entries), "year_metrics":{str(y):metric(g) for y,g in entries.groupby(entries.trade_date.dt.year)}, "threshold_mining":False, "validation_read":False, "final_oos_read":False, "production_changes":False, "reason_codes":["PIT_SAFE_FEATURES","FROZEN_FAMILY","ONE_ENTRY_PER_EPISODE","DESCRIPTIVE_ONLY"]}
    (ART / "moderate_vol_trend_family_eval.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))
    return out


if __name__ == "__main__": run()
