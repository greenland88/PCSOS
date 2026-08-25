"""Year and declared-overlay audit for the QQQ controlled-reset lead."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT = Path("research_outputs/qqq_entry_discovery_agent_v1")
ART = ROOT / "artifacts"


def first_episode(frame, trading_sessions):
    x = frame.sort_values("trade_date").copy()
    positions = {day: i for i, day in enumerate(pd.DatetimeIndex(trading_sessions).normalize())}
    x["session_index"] = x.trade_date.map(positions)
    if x["session_index"].isna().any():
        raise ValueError("EPISODE_SESSION_CALENDAR_MISSING")
    x["episode_id"] = x.session_index.diff().fillna(999).ne(1).cumsum()
    return x.groupby("episode_id", as_index=False).first()


def stats(x):
    p = x.realized_pnl
    neg = p[p < 0]
    pos = p[p > 0]
    return {
        "episodes": int(len(x)), "pnl": float(p.sum()),
        "pf": float(pos.sum() / abs(neg.sum())) if len(neg) else None,
        "good_wins": int((x.outcome_class == "GOOD_WIN").sum()),
        "normal_losses": int((x.outcome_class == "NORMAL_LOSS").sum()),
        "stop_losses": int((x.outcome_class == "STOP_LOSS").sum()),
        "tail_losses": int((x.outcome_class == "TAIL_LOSS").sum()),
    }


def run():
    d = pd.read_parquet(ART / "qqq_state_transition_features_train_2020_2023.parquet")
    d.trade_date = pd.to_datetime(d.trade_date).dt.normalize()
    sessions = PCSDataAccess().read_prices("QQQ", d.trade_date.min(), d.trade_date.max()).date
    family = d[(d.drawdown60 <= -0.02) & (d.ret10 > 0)].copy()
    family["VOLUME_STRESS"] = family.volume_ratio20 >= 1.5
    family["TREND_WEAKENING_AND_VOLUME_STRESS"] = family.TREND_WEAKENING & family.VOLUME_STRESS
    entries = first_episode(family, sessions)
    states = {
        "TREND_WEAKENING": entries.TREND_WEAKENING,
        "VOLUME_STRESS": entries.VOLUME_STRESS,
        "VOLATILITY_EXPANDING": entries.VOLATILITY_EXPANDING,
        "TREND_WEAKENING_AND_VOLUME_STRESS": entries.TREND_WEAKENING_AND_VOLUME_STRESS,
    }
    out = {
        "module": "pcs.research.qqq_trend_weakening_audit", "version": "v1",
        "status": "DESCRIPTIVE_ONLY", "data_source": "PCS_CANONICAL_DATA",
        "research_mode": "EXISTING_TRADE", "family_rule": "drawdown60 <= -0.02 AND ret10 > 0",
        "independent_episodes": int(len(entries)), "states": {},
        "threshold_mining": False, "validation_read": False,
        "final_oos_read": False, "production_changes": False,
        "reason_codes": ["PIT_SAFE_FEATURES", "ONE_ENTRY_PER_EPISODE", "PREDECLARED_STATES", "DESCRIPTIVE_ONLY"],
    }
    for name, mask in states.items():
        removed, kept = entries[mask], entries[~mask]
        out["states"][name] = {
            "removed": stats(removed), "retained": stats(kept),
            "retained_2022": stats(kept[kept.trade_date.dt.year == 2022]),
            "year_removed": {str(y): stats(g) for y, g in removed.groupby(removed.trade_date.dt.year)},
            "year_retained": {str(y): stats(g) for y, g in kept.groupby(kept.trade_date.dt.year)},
        }
    target = ART / "qqq_trend_weakening_audit.json"
    temp = ART / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temp.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    print(json.dumps(out, indent=2, default=str))
    return out


if __name__ == "__main__":
    run()
