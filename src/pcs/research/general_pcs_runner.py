"""Ticker-independent PCS research-archetype admission and attribution.

This module owns signal/episode attribution only.  Contract selection and
lifecycle execution remain delegated to the canonical PCS replay layer.
"""
from __future__ import annotations

from typing import Any
from pathlib import Path
import json
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.control_plane import require_market_data
from pcs.strategies.research_templates.catalog import (
    GENERAL_PCS_RESEARCH_STRATEGIES, Evaluation, evaluate,
)
from pcs.strategies.frozen_adaptive_config import load_frozen_strategy_config
from .strategy_transfer_runner import _features
from .research_framework import from_mapping
from .runner import ResearchRunner


def evaluate_general_pcs(ticker: str, train_start: str = "2018-01-01",
                         train_end: str | None = None, *, data_access=None,
                         mode: str = "FIXED") -> dict[str, Any]:
    """Evaluate all registered general PCS predicates on one PIT daily path.

    Dates are attributed independently, while ``matched_strategy_ids`` makes
    overlap explicit so downstream execution can select one economic trade.
    """
    if mode not in {"FIXED", "ADAPTIVE"}: raise ValueError(f"UNKNOWN_STRATEGY_MODE:{mode}")
    access = data_access or PCSDataAccess.canonical()
    require_market_data(ticker, {"start": train_start, "end": train_end,
                                 "datasets": {"daily": {"required": True}, "options": {"required": True}},
                                 "consumer": "GENERAL_PCS"}, access=access)
    daily = access.read_prices(ticker, train_start, train_end)
    if mode == "ADAPTIVE":
        frozen = load_frozen_strategy_config(ticker)
        resolved_configs = {strategy_id: {**frozen.to_dict(), "strategy_id": strategy_id} for strategy_id in GENERAL_PCS_RESEARCH_STRATEGIES}
    else:
        resolved_configs = {}
    configs_by_date = {}
    if mode == "ADAPTIVE":
        daily_dates = pd.to_datetime(daily["date"]).drop_duplicates().sort_values().reset_index(drop=True)
        frozen_start = pd.Timestamp(frozen.as_of)
        for day in daily_dates:
            if day >= frozen_start:
                configs_by_date[day.date().isoformat()] = resolved_configs
    windows = tuple(w for by_strategy in configs_by_date.values() for c in by_strategy.values() for w in (c["momentum_window_days"], c["recovery_window_days"])) if mode == "ADAPTIVE" else ()
    frame = _features(daily, return_windows=windows)
    rows = []
    for _, row in frame.iterrows():
        matches = []
        evaluations = {}
        for strategy_id in GENERAL_PCS_RESEARCH_STRATEGIES:
            config = configs_by_date.get(pd.Timestamp(row.date).date().isoformat(), {}).get(strategy_id) if mode == "ADAPTIVE" else None
            # Resolve config dicts are converted to a lightweight object only
            # at this boundary; fixed mode never consults adaptive fields.
            if config is not None:
                from types import SimpleNamespace
                config = SimpleNamespace(**{k: config[k] for k in ("momentum_window_days", "recovery_window_days", "pullback_depth", "volume_ratio_floor")})
            if mode == "ADAPTIVE" and config is None:
                ev = Evaluation(strategy_id, ticker.upper(), row.date, "NO_QUALIFY", "adaptive config warmup unavailable", ("ADAPTIVE_CONFIG_WARMUP",), {})
            else:
                ev = evaluate(strategy_id, ticker, row.date, row.to_dict(), mode=mode, config=config)
                if mode == "ADAPTIVE" and not ev.consumed_config_fields:
                    raise RuntimeError("CONFIG_NOT_CONSUMED")
            evaluations[strategy_id] = ev
            if ev.status == "QUALIFY":
                matches.append(strategy_id)
        rows.append({"ticker": ticker.upper(), "date": pd.Timestamp(row.date),
                     "matched_strategy_ids": matches,
                     "signal_overlap": len(matches) > 1,
                     "evaluations": {k: {"status": v.status, "reason_codes": list(v.reason_codes),
                                         "consumed_config_fields": list(v.consumed_config_fields)} for k, v in evaluations.items()}})
    signals = pd.DataFrame(rows)
    return {"strategy_family": "GENERAL_PCS", "ticker": ticker.upper(), "mode": mode,
            "strategies": list(GENERAL_PCS_RESEARCH_STRATEGIES),
            "resolved_configs": resolved_configs, "resolved_configs_by_date": configs_by_date if mode == "ADAPTIVE" else {},
            "signals": signals, "overlap_dates": int(signals.signal_overlap.sum()),
            "economic_trade_policy": "one canonical selected trade per date; preserve all matched_strategy_ids"}


def run_general_pcs_replay(ticker: str, train_start: str = "2018-01-01",
                           train_end: str | None = None, *, output_dir="research_outputs/general_pcs_execution",
                           data_access=None, mode: str = "FIXED") -> dict[str, Any]:
    """Run one canonical replay for the union of general strategy episodes.

    The union is deliberate: overlapping strategy signals are attributed to
    the same economic candidate/lifecycle rather than replayed independently.
    """
    access = data_access or PCSDataAccess.canonical()
    signal_result = evaluate_general_pcs(ticker, train_start, train_end, data_access=access, mode=mode)
    signals = signal_result["signals"]
    episode_dates: dict[str, list[str]] = {}
    for strategy_id in GENERAL_PCS_RESEARCH_STRATEGIES:
        dates = pd.to_datetime(signals.loc[signals.evaluations.map(lambda x: x[strategy_id]["status"] == "QUALIFY"), "date"]).sort_values()
        if len(dates):
            groups = dates.diff().dt.days.fillna(999).gt(4).cumsum()
            episode_dates[strategy_id] = [str(group.iloc[0].date()) for _, group in dates.groupby(groups)]
        else:
            episode_dates[strategy_id] = []
    signal_union_dates = sorted({date for dates in episode_dates.values() for date in dates})
    option_source = access.resolve_source("options", ticker)
    union_dates = [date for date in signal_union_dates if pd.Timestamp(option_source.first_date) <= pd.Timestamp(date) <= pd.Timestamp(option_source.last_date)]
    if not signal_union_dates:
        return {"strategy_family": "GENERAL_PCS", "ticker": ticker.upper(), "strategies": {}, "economic_trades": [], "reason_codes": ["NO_QUALIFYING_EPISODES"], "final_oos_read": False}
    if not union_dates:
        return {"strategy_family": "GENERAL_PCS", "ticker": ticker.upper(), "strategies": {}, "economic_trades": [], "signal_union_dates": signal_union_dates, "reason_codes": ["NO_EXECUTABLE_OPTION_COVERAGE"], "final_oos_read": False}
    raw = {
        "research_id": f"general_pcs_{ticker.lower()}_{mode.lower()}_execution",
        "ticker": ticker.upper(), "research_mode": "CURRENT_STRATEGY_REPLAY",
        "hypothesis": "Canonical execution of the ticker-independent GENERAL_PCS strategy family.",
        "population_source": {"type": "ticker_daily_calendar", "point_in_time": True, "frozen": False},
        "signal_definition": {"benchmark_symbol": "QQQ", "strategy_family": "GENERAL_PCS", "execution_dates": union_dates, "track_a_execution_only": True, "creates_new_entry_dates": True},
        "entry_date_rule": {"rule": "first qualifying date per independent strategy episode; union across strategies"},
        "date_range": {"start": train_start, "end": train_end},
        "split_policy": {"train_end": train_end},
        "contract_selection_policy": {"mode": "RULE_SET", "width_priority": [5, 10, 2], "as_of_only": True},
        "lifecycle_policy": {"source": "canonical_lifecycle_adapter", "no_future_selection": True},
        "frozen_parameters": {"dte": [30, 45], "safe_strike_atr": 2.3, "credit_width": 0.10},
        "allowed_parameters": {"research_only": True}, "final_oos_access": False,
        "production_changes_allowed": False,
        "rules": {"dte_min": 30, "dte_max": 45, "safe_strike_atr": 2.3, "allowed_widths": [5, 10, 2], "width_mode": "ALL", "min_credit_width_ratio": .10, "trend_gate": True, "pullback_gate": True, "support_gate": True, "regime_gate": False, "event_gate": True, "liquidity_gate": True, "predictability_gate": True},
    }
    spec = from_mapping(raw)
    replay = ResearchRunner(spec, output_dir=output_dir).execute_current_strategy_replay(data_access=access)
    replay_dir = Path(output_dir) / spec.research_id
    lifecycle = pd.read_parquet(replay_dir / "lifecycle_results.parquet") if (replay_dir / "lifecycle_results.parquet").exists() else pd.DataFrame()
    candidates = pd.read_parquet(replay_dir / "candidates.parquet") if (replay_dir / "candidates.parquet").exists() else pd.DataFrame()
    signal_map = {pd.Timestamp(row.date).date().isoformat(): list(row.matched_strategy_ids) for _, row in signals.iterrows() if row.matched_strategy_ids}
    if len(candidates):
        candidates["entry_date"] = pd.to_datetime(candidates["date"]).dt.date.astype(str)
        candidates["matched_strategy_ids"] = candidates.entry_date.map(signal_map).apply(lambda x: x if isinstance(x, list) else [])
        candidates.to_parquet(replay_dir / "candidates_attributed.parquet", index=False)
    if len(lifecycle):
        lifecycle["entry_date"] = pd.to_datetime(lifecycle["entry_date"]).dt.date.astype(str)
        lifecycle["matched_strategy_ids"] = lifecycle.entry_date.map(signal_map).apply(lambda x: x if isinstance(x, list) else [])
        lifecycle.to_parquet(replay_dir / "lifecycle_results_attributed.parquet", index=False)
    per_strategy = {}
    for strategy_id in GENERAL_PCS_RESEARCH_STRATEGIES:
        dates = set(episode_dates[strategy_id])
        trades = lifecycle[lifecycle.entry_date.isin(dates)].copy() if len(lifecycle) else lifecycle
        pnl = pd.to_numeric(trades.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").dropna()
        wins = pnl[pnl > 0]; losses = pnl[pnl < 0]
        episode_pnl = trades.groupby("entry_date").realized_pnl.sum() if len(trades) else pd.Series(dtype=float)
        year_pnl = trades.assign(year=pd.to_datetime(trades.entry_date).dt.year).groupby("year").realized_pnl.sum() if len(trades) else pd.Series(dtype=float)
        per_strategy[strategy_id] = {"qualifying_signals": int(sum(signals.evaluations.map(lambda x: x[strategy_id]["status"] == "QUALIFY"))), "independent_episodes": len(dates), "contract_candidates": int(len(candidates[candidates.entry_date.isin(dates)])) if len(candidates) else 0, "selected_economic_trades": int(len(trades)), "completed_lifecycles": int((trades.status == "COMPLETE").sum()) if len(trades) else 0, "total_pnl": float(pnl.sum()) if len(pnl) else 0.0, "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) else None, "expectancy": float(pnl.mean()) if len(pnl) else None, "win_rate": float((pnl > 0).mean()) if len(pnl) else None, "stop_rate": float(trades.stop_triggered.mean()) if len(trades) and "stop_triggered" in trades else None, "average_holding_days": float(pd.to_numeric(trades.holding_trading_days, errors="coerce").mean()) if len(trades) and "holding_trading_days" in trades else None, "episode_pnl": {str(k): float(v) for k, v in episode_pnl.items()}, "year_pnl": {str(k): float(v) for k, v in year_pnl.items()}, "top_episode_contribution_pct": float(episode_pnl.max() / pnl.sum() * 100) if len(episode_pnl) and pnl.sum() else None}
    result = {"strategy_family": "GENERAL_PCS", "ticker": ticker.upper(), "mode": mode, "episode_dates": episode_dates, "union_execution_dates": union_dates, "strategies": per_strategy, "economic_trade_count": int(len(lifecycle)), "replay": replay, "final_oos_read": False, "production_change": False}
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    (Path(output_dir) / f"{ticker.lower()}_general_pcs_family.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


__all__ = ["evaluate_general_pcs"]
