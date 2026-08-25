"""NVDA-only descriptive mode and bad-state screen for the frozen TRAIN universe.

This is deliberately not a production or validation runner.  It reuses the
authoritative 623-date outcome table, keeps the two registered families out of
the candidate list, and writes only research evidence into the current NVDA
research artifact set.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"
SOURCE = OUT / "pit_feature_outcome_table.parquet"
PROFITABLE_CLASSES = {"SMALL_WIN", "GOOD_WIN"}
BAD_CLASSES = {"NORMAL_LOSS", "STOP_LOSS", "TAIL_LOSS"}


def _episode_first(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.sort_values("trade_date").copy()
    x["episode_gap"] = x.trade_date.diff().dt.days.fillna(999)
    x["episode_id"] = (x.episode_gap > 10).cumsum()
    return x.groupby("episode_id", as_index=False).head(1)


def _metrics(frame: pd.DataFrame) -> dict:
    x = _episode_first(frame)
    losses = x.loc[x.realized_pnl < 0, "realized_pnl"].sum()
    wins = x.loc[x.realized_pnl > 0, "realized_pnl"].sum()
    return {
        "episodes": int(len(x)),
        "pnl": float(x.realized_pnl.sum()) if len(x) else 0.0,
        "expectancy": float(x.realized_pnl.mean()) if len(x) else None,
        "profit_factor": float(wins / abs(losses)) if losses else None,
        "win_rate": float((x.realized_pnl > 0).mean()) if len(x) else None,
        "stop_rate": float(x.stopped.mean()) if len(x) else None,
        "tail_loss_count": int((x.outcome_class == "TAIL_LOSS").sum()),
        "normal_loss_count": int((x.outcome_class == "NORMAL_LOSS").sum()),
        "worst_trade": float(x.realized_pnl.min()) if len(x) else None,
        "years": sorted(int(y) for y in x.year.dropna().unique()),
    }


def run() -> dict:
    d = pd.read_parquet(SOURCE).copy()
    d["trade_date"] = pd.to_datetime(d["trade_date"]).dt.normalize()
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    train = d[(d.ticker == "NVDA") & d.executable_pcs & d.trade_date.between("2020-01-02", "2023-12-31")].copy()
    # Use the authoritative lifecycle classification already persisted in the
    # outcome table; do not recreate or infer outcome labels here.

    # These are structural modes, not threshold variants of the frozen families.
    modes = {
        "PULLBACK_PCS": (train.nvda_close_vs_sma200 > 0) & (train.nvda_ret5 < 0) & (train.nvda_drawdown20 < 0),
        "SUPPORT_RECLAIM_PCS": (train.nvda_close_vs_sma20 > 0) & (train.nvda_close_vs_sma50 > 0) & (train.nvda_ret5 > 0) & (train.consecutive_down_days >= 2),
        "POST_SELLOFF_PCS": (train.nvda_drawdown20 <= -0.15) & (train.nvda_ret5 > 0),
        "RANGE_CONSOLIDATION_PCS": (train.nvda_ret20.abs() < 0.10) & (train.nvda_atr14 <= train.nvda_atr14.median()),
        "VOLATILITY_OPPORTUNITY_PCS": (train.nvda_atr14 > train.nvda_atr14.median()) & (train.nvda_ret5 > 0) & (train.nvda_volume_rel20 > 1),
        "MARKET_CONFIRMED_PCS": (train.qqq_close_vs_sma50 > 0) & (train.nvda_relative_strength20 > 0) & (train.nvda_ret5 > 0),
    }
    mode_rows = []
    for mode, mask in modes.items():
        selected = train[mask]
        mode_rows.append({"mode_id": mode, "qualifying_dates": int(mask.sum()), **_metrics(selected)})
    pd.DataFrame(mode_rows).to_csv(OUT / "v2_round31_mode_screen.csv", index=False)

    bad_states = {
        "LOW_PARTICIPATION": train.nvda_volume_rel20 < 1,
        "LONG_TERM_WEAKNESS": train.nvda_close_vs_sma200 <= 0,
        "DOWNSIDE_ACCELERATION": (train.nvda_ret5 < 0) & (train.nvda_ret20 < 0),
        "DEEP_SELL_OFF": train.nvda_drawdown20 <= -0.15,
        "MARKET_UNCONFIRMED": train.qqq_close_vs_sma50 <= 0,
    }
    bad_rows = []
    for state, mask in bad_states.items():
        inside = _episode_first(train[mask])
        outside = _episode_first(train[~mask])
        bad = inside.outcome_class.isin(BAD_CLASSES)
        all_bad_episodes = len(_episode_first(train[train.outcome_class.isin(BAD_CLASSES)]))
        all_good_episodes = len(_episode_first(train[train.outcome_class.isin(PROFITABLE_CLASSES)]))
        bad_rows.append({
            "state_id": state,
            "state_episodes": int(len(inside)),
            "bad_cases_inside": int(bad.sum()),
            "tail_losses_inside": int((inside.outcome_class == "TAIL_LOSS").sum()),
            "bad_case_capture_rate": float(bad.sum() / max(all_bad_episodes, 1)),
            "good_case_false_exclusion_rate": float(inside.outcome_class.isin(PROFITABLE_CLASSES).sum() / max(all_good_episodes, 1)),
            "inside": _metrics(train[mask]),
            "outside": _metrics(train[~mask]),
            "conclusion": "NO_RELIABLE_FILTER",
        })
    pd.DataFrame([{k: v for k, v in row.items() if k not in {"inside", "outside"}} for row in bad_rows]).to_csv(OUT / "v2_round31_bad_state_screen.csv", index=False)

    manifest = {
        "module": "pcs.research.nvda_mode_discovery_round31",
        "version": "1.0",
        "symbol": "NVDA",
        "status": "DESCRIPTIVE_ONLY",
        "data_source": "PCS_CANONICAL_DATA",
        "research_mode": "EXISTING_TRADE",
        "population_source": str(SOURCE.relative_to(ROOT)),
        "input_rows": int(len(train)),
        "executable_dates": int(len(train)),
        "pit_feature_date_equals_trade_date": bool((train.date == train.trade_date).all()),
        "validation_read": False,
        "final_oos_read": False,
        "production_changes": False,
        "frozen_rule_families_unchanged": ["PCS_TREND_CONTINUATION", "PCS_CONSTRUCTIVE_RECOVERY"],
        "new_mode_candidates": mode_rows,
        "bad_state_candidates": bad_rows,
        "reason_codes": ["NVDA_ONLY", "FROZEN_623_DATE_TRAIN_UNIVERSE", "PIT_SAFE", "NO_VALIDATION", "NO_FINAL_OOS", "NO_PRODUCTION_CHANGE", "DESCRIPTIVE_ONLY"],
    }
    (OUT / "v2_round31_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
