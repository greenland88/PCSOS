"""Predeclared PIT-safe QQQ BAD_STATE / NO_TRADE diagnostics."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd

ROOT = Path("research_outputs/qqq_entry_discovery_agent_v1")
ART = ROOT / "artifacts"


def metrics(frame):
    p = frame.realized_pnl
    wins, losses = p[p > 0], p[p < 0]
    return {
        "trades": int(len(frame)),
        "pnl": float(p.sum()),
        "pf": float(wins.sum() / abs(losses.sum())) if len(losses) else None,
        "expectancy": float(p.mean()) if len(p) else None,
        "win_rate": float((p > 0).mean()) if len(p) else None,
        "stop_rate": float(frame.stopped.astype(bool).mean()) if len(frame) else None,
        "tail_loss_rate": float((frame.outcome_class == "TAIL_LOSS").mean()) if len(frame) else None,
    }


def episode_first(frame, calendar):
    x = frame.sort_values("trade_date").copy()
    sessions = pd.DatetimeIndex(pd.to_datetime(calendar).dt.normalize().drop_duplicates().sort_values())
    positions = pd.Series(range(len(sessions)), index=sessions)
    x["session_index"] = x.trade_date.map(positions)
    if x["session_index"].isna().any():
        raise ValueError("QQQ_BAD_STATE_SESSION_INDEX_INCOMPLETE")
    x["episode_id"] = x.session_index.diff().fillna(999).ne(1).cumsum()
    return x.groupby("episode_id", as_index=False).first()


def main():
    d = pd.read_parquet(ART / "qqq_pit_feature_outcome_table_train_2020_2023.parquet").copy()
    d.trade_date = pd.to_datetime(d.trade_date).dt.normalize()
    # Fixed, interpretable states declared before inspecting aggregate results.
    states = {
        "TREND_BREAK": (d.close_sma50_atr <= 0) | (d.close_sma200_atr <= 2.5),
        "DOWNSIDE_ACCELERATION": (d.ret5 < 0) & (d.ret10 < 0) & (d.ret20 < 0),
        "VOLATILITY_STRESS": (d.atr_pct_rank >= 0.75) | (d.vol_pct_rank >= 0.75),
        "VOLUME_STRESS": d.volume_ratio20 >= 1.5,
    }
    states["NO_TRADE_UNION"] = pd.concat(states.values(), axis=1).any(axis=1)
    out = {
        "module": "pcs.research.qqq_bad_state_analysis",
        "status": "DESCRIPTIVE_RESEARCH_COMPLETED",
        "data_source": "PCS_CANONICAL_DATA",
        "thresholds_predeclared": True,
        "threshold_mining": False,
        "validation_read": False,
        "final_oos_read": False,
        "production_changes": False,
        "states": {},
    }
    for name, mask in states.items():
        kept = d.loc[~mask]
        bad = d.loc[mask]
        kept_ep = episode_first(kept, d.trade_date)
        bad_ep = episode_first(bad, d.trade_date)
        out["states"][name] = {
            "bad_dates": int(mask.sum()),
            "bad_date_share": float(mask.mean()),
            "bad_outcomes": metrics(bad),
            "retained_outcomes": metrics(kept),
            "retained_one_entry_per_episode": metrics(kept_ep),
            "excluded_one_entry_per_episode": metrics(bad_ep),
            "removed_stop_losses": int((bad.outcome_class == "STOP_LOSS").sum()),
            "removed_tail_losses": int((bad.outcome_class == "TAIL_LOSS").sum()),
            "year_retained": {str(y): metrics(g) for y, g in kept.assign(year=kept.trade_date.dt.year).groupby("year")},
        }
    target = ART / "bad_state_no_trade_analysis.json"
    temp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
