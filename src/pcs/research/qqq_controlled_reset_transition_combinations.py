"""Finite descriptive screen of predeclared PIT-safe transition states."""
from itertools import combinations
from pathlib import Path
import json
import pandas as pd

ROOT = Path("research_outputs/qqq_entry_discovery_agent_v1")
ART = ROOT / "artifacts"
TRANSITIONS = [
    "TREND_WEAKENING",
    "VOLATILITY_EXPANDING",
    "DRAWDOWN_DEEPENING",
    "RECOVERY_AFTER_RESET",
]


def first_episode(frame):
    x = frame.sort_values("trade_date").copy()
    x["episode_id"] = (x.trade_date.diff().dt.days.fillna(999) > 4).cumsum()
    return x.groupby("episode_id", as_index=False).first()


def stats(x):
    pnl = x.realized_pnl
    losses = pnl[pnl < 0]
    wins = pnl[pnl > 0]
    return {
        "episodes": int(len(x)),
        "pnl": float(pnl.sum()),
        "pf": float(wins.sum() / abs(losses.sum())) if len(losses) else None,
        "stops": int((x.outcome_class == "STOP_LOSS").sum()),
        "tails": int((x.outcome_class == "TAIL_LOSS").sum()),
        "good_wins": int((x.outcome_class == "GOOD_WIN").sum()),
        "years": sorted(int(y) for y in x.trade_date.dt.year.unique()),
    }


def run():
    d = pd.read_parquet(ART / "qqq_state_transition_features_train_2020_2023.parquet")
    d.trade_date = pd.to_datetime(d.trade_date).dt.normalize()
    family = d[(d.drawdown60 <= -0.02) & (d.ret10 > 0)]
    entries = first_episode(family)
    all_stats = stats(entries)
    rows = []
    for size in range(1, len(TRANSITIONS) + 1):
        for names in combinations(TRANSITIONS, size):
            mask = entries[list(names)].fillna(False).any(axis=1)
            removed = entries[mask]
            retained = entries[~mask]
            loss_total = int((entries.outcome_class.isin(["STOP_LOSS", "TAIL_LOSS"])).sum())
            good_total = int((entries.outcome_class == "GOOD_WIN").sum())
            rows.append({
                "state": " OR ".join(names),
                "states": list(names),
                "removed": stats(removed),
                "retained": stats(retained),
                "retained_2022": stats(retained[retained.trade_date.dt.year == 2022]),
                "loss_capture_rate": float(((removed.outcome_class.isin(["STOP_LOSS", "TAIL_LOSS"])).sum()) / loss_total) if loss_total else None,
                "good_win_false_exclusion_rate": float(((removed.outcome_class == "GOOD_WIN").sum()) / good_total) if good_total else None,
            })
    out = {
        "module": "pcs.research.qqq_controlled_reset_transition_combinations",
        "version": "v1",
        "status": "DESCRIPTIVE_ONLY",
        "data_source": "PCS_CANONICAL_DATA",
        "research_mode": "EXISTING_TRADE",
        "family_rule": "drawdown60 <= -0.02 AND ret10 > 0",
        "independent_episodes": int(len(entries)),
        "all_episodes": all_stats,
        "predeclared_transitions": TRANSITIONS,
        "combination_count": len(rows),
        "combinations": rows,
        "threshold_mining": False,
        "validation_read": False,
        "final_oos_read": False,
        "production_changes": False,
        "reason_codes": ["PIT_SAFE_FEATURES", "ONE_ENTRY_PER_EPISODE", "FINITE_PREDECLARED_SCREEN", "DESCRIPTIVE_ONLY"],
    }
    (ART / "controlled_reset_transition_combinations.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(rows).to_csv(ART / "controlled_reset_transition_combinations.csv", index=False)
    print(json.dumps(out, indent=2, default=str))
    return out


if __name__ == "__main__":
    run()
