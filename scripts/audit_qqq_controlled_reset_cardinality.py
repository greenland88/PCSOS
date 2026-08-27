"""Audit-only report for the QQQ Controlled Reset replay population.

This reads the canonical daily calendar and the corrected replay artifacts. It
does not discover a new hypothesis, tune parameters, or write strategy state.
"""
from __future__ import annotations

import json
from pathlib import Path
import pandas as pd

from pcs.data.access import PCSDataAccess

ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "research_outputs/frozen_strategy_regression/QQQ/qqq_frozen_controlled_reset_canonical_20260825"
OUT = ROOT / "research_outputs/frozen_strategy_regression/QQQ/controlled_reset_cardinality_audit.json"


def main() -> None:
    access = PCSDataAccess.canonical()
    # Include the pre-scope warmup required by the 60-session rolling high;
    # only the requested replay window enters the funnel.
    d = access.read_prices("QQQ", "2018-01-01", "2026-05-31").copy()
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    d = d.sort_values("date").reset_index(drop=True)
    rolling = d["close"].rolling(60, min_periods=60).max()
    d["drawdown60"] = d["close"] / rolling - 1
    d["ret10"] = d["close"].pct_change(10)
    qualifying = d.loc[(d["date"] >= pd.Timestamp("2020-01-01")) & (d["drawdown60"] <= -0.02) & (d["ret10"] > 0), ["date"]].copy()
    # Frozen Controlled Reset semantics: qualifying rows separated by more
    # than four calendar days begin a new independent episode. A gap of <=4
    # calendar days remains in the same episode (including weekends/holidays).
    qualifying["episode_number"] = (qualifying["date"].diff().dt.days.fillna(999) > 4).cumsum() + 1
    episodes = []
    for number, group in qualifying.groupby("episode_number", sort=True):
        episodes.append({
            "episode_number": int(number),
            "start": str(group.date.min().date()),
            "end": str(group.date.max().date()),
            "qualifying_dates": [str(x.date()) for x in group.date],
            "qualifying_signal_days": int(len(group)),
        })
    candidates = pd.read_parquet(REPLAY / "candidates.parquet")
    lifecycle = pd.read_parquet(REPLAY / "lifecycle_results.parquet")
    for frame in (candidates, lifecycle):
        frame["entry_date"] = pd.to_datetime(frame["entry_date"] if "entry_date" in frame else frame["date"]).dt.normalize()
    episode_by_date = {date: item["episode_number"] for item in episodes for date in pd.to_datetime(item["qualifying_dates"])}
    for item in episodes:
        dates = set(pd.to_datetime(item["qualifying_dates"]))
        item["executable_dates"] = sorted(str(x.date()) for x in lifecycle.loc[lifecycle.entry_date.isin(dates), "entry_date"].unique())
        item["selected_trade_entry_dates"] = sorted(str(x.date()) for x in lifecycle.loc[lifecycle.entry_date.isin(dates), "entry_date"].unique())
        item["selected_trade_count"] = len(item["selected_trade_entry_dates"])
        item["selected_contracts"] = [
            {k: (str(v.date()) if isinstance(v, pd.Timestamp) else v) for k, v in row.items()
             if k in {"candidate_id", "entry_date", "expiration_date", "short_strike", "long_strike", "spread_width"}}
            for row in lifecycle.loc[lifecycle.entry_date.isin(dates)].to_dict("records")
        ]
    yearly = []
    for year in sorted(set(pd.to_datetime(lifecycle.entry_date).dt.year)):
        ls = lifecycle[pd.to_datetime(lifecycle.entry_date).dt.year.eq(year)]
        ep = {episode_by_date.get(pd.Timestamp(x).normalize()) for x in ls.entry_date}
        yearly.append({"year": int(year), "lifecycles": len(ls), "independent_episodes": len(ep - {None}), "selected_trades": len(ls)})
    counts = {
        "raw_qualifying_signal_days": len(qualifying),
        "independent_episodes": len(episodes),
        "executable_episode_dates": int(sum(bool(x["executable_dates"]) for x in episodes)),
        "contract_candidates": len(candidates),
        "selected_economic_trades": len(lifecycle),
        "completed_lifecycles": int(pd.to_numeric(lifecycle.get("realized_pnl"), errors="coerce").notna().sum()),
    }
    output = {
        "module": "pcs.scripts.audit_qqq_controlled_reset_cardinality",
        "status": "COMPLETED_AUDIT",
        "ticker": "QQQ",
        "strategy": "Controlled Reset",
        "strategy_definition": "drawdown60 <= -0.02 AND ret10 > 0",
        "episode_definition": {"start": "first qualifying date", "end": "last qualifying date before a gap > 4 calendar days", "same_episode_gap_days": "<=4 calendar days"},
        "counts": counts,
        "invariant": {"selected_economic_trades_le_executable_episode_dates": counts["selected_economic_trades"] <= counts["executable_episode_dates"], "executable_episode_dates_le_independent_episodes": counts["executable_episode_dates"] <= counts["independent_episodes"], "one_entry_per_episode": counts["selected_economic_trades"] <= counts["independent_episodes"]},
        "ratios": {"signal_days_per_episode": counts["raw_qualifying_signal_days"] / counts["independent_episodes"], "episodes_per_signal_day": counts["independent_episodes"] / counts["raw_qualifying_signal_days"], "trades_per_episode": counts["selected_economic_trades"] / counts["independent_episodes"]},
        "yearly_distribution": yearly,
        "episodes": episodes,
        "observed_908": {"raw_qualifying_signal_days": "NOT_PERSISTED", "independent_episodes": 72, "executable_episode_dates": "NOT_PERSISTED", "contract_candidates": 908, "selected_economic_trades": 908, "completed_lifecycles": 908, "yearly_episode_trade_join": "NOT_RECONSTRUCTIBLE_AFTER_CORRECTED_REPLAY_REPLACED_THE_908_ARTIFACT"},
        "observed_908_assessment": "The prior 908 completed-lifecycle result is not a valid economic ledger: 908 selected/lifecycle rows exceeded the 72 independent episodes and had no one-entry-per-episode identity. Its exact per-year/per-episode join is unavailable because the artifact was replaced; aggregate 908 is sufficient to prove the invariant violation.",
        "conclusion": "CONTROLLED_RESET_REPLAY_BUG",
        "final_oos_read": False,
        "production_logic_changed": False,
        "thresholds_changed": False,
    }
    OUT.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("counts", "invariant", "ratios", "conclusion")}, indent=2))


if __name__ == "__main__":
    main()
