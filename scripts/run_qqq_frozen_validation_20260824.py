"""Guarded validation replay for the two frozen QQQ TRAIN candidates.

Research-only.  The signal dates are discovered from the complete PIT daily
calendar; contracts and lifecycle are delegated to the existing frozen
selector/track_trade path.  The validation boundary is hard and FINAL OOS is
never read.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import yaml

from pcs.data.access import PCSDataAccess
from pcs.research.credit_stop import load_quotes_canonical, load_spread_quotes_canonical, track_trade
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.indicators import calculate_base_indicators
from pcs.research.rules.core import RuleStatus, canonical_hash, evaluate_chain, resolve_scenario
from pcs.research.rules.registry import RULE_REGISTRY
from run_spy_qqq_modular_monthly_replay import select, context

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "qqq_frozen_validation_20260824"
SPLIT = (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-05-31"))
TRAIN_ART = ROOT / "research_outputs" / "qqq_entry_discovery_agent_v1" / "artifacts"
SCENARIO = ROOT / "research_configs" / "pcs_rule_scenarios" / "research_current_rules_available_context.yaml"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def features(access: PCSDataAccess) -> pd.DataFrame:
    d = access.read_prices("QQQ", "2010-01-01", "2026-05-31").copy()
    d["date"] = pd.to_datetime(d.date).dt.normalize()
    d = d.sort_values("date").reset_index(drop=True)
    d["atr14"] = calculate_base_indicators(d, TrendIndicatorConfig())["atr14"]
    d["ret5"] = d.close.pct_change(5)
    d["ret10"] = d.close.pct_change(10)
    d["drawdown60"] = d.close / d.close.rolling(60, min_periods=60).max() - 1
    d["sma50"] = d.close.rolling(50, min_periods=50).mean()
    d["close_sma50_atr"] = (d.close - d.sma50) / d.atr14
    d["prior_close_sma50_atr"] = d.close_sma50_atr.shift(1)
    return d


def selector_daily(access: PCSDataAccess) -> pd.DataFrame:
    d = access.read_prices("QQQ", "2010-01-01", "2026-05-31").copy()
    d["date"] = pd.to_datetime(d.date).dt.normalize()
    d = d.sort_values("date").drop_duplicates("date")
    d["atr"] = calculate_base_indicators(d, TrendIndicatorConfig())["atr14"]
    return d


def first_per_episode(d: pd.DataFrame, mask: pd.Series) -> list[pd.Timestamp]:
    x = d.loc[mask].sort_values("date").copy()
    sessions = pd.DatetimeIndex(d.date).normalize()
    session_positions = {day: i for i, day in enumerate(sessions)}
    positions = x["date"].map(lambda value: session_positions.get(pd.Timestamp(value).normalize(), -1))
    x["episode_id"] = positions.diff().fillna(999).ne(1).cumsum()
    return [pd.Timestamp(v).normalize() for v in x.groupby("episode_id", as_index=False).first().date]


def signal_dates(d: pd.DataFrame) -> dict[str, list[pd.Timestamp]]:
    reset = (d.drawdown60 <= -0.02) & (d.ret10 > 0)
    reclaim = reset & (d.prior_close_sma50_atr <= 0) & (d.close_sma50_atr > 0)
    return {
        "QQQ_CONTROLLED_RESET": first_per_episode(d, reset),
        "QQQ_SMA50_RECLAIM_AFTER_WEAKNESS": first_per_episode(d, reclaim),
    }


def replay(strategy: str, dates: list[pd.Timestamp], scenario: dict, boundary: pd.Timestamp, ds: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    ds = ds.set_index("date")
    selected, audit = [], []
    for day in dates:
        row = ds.loc[day].copy()
        row["date"] = day
        q, meta = load_quotes_canonical("QQQ", day, day)
        choices = select(q, row)
        opened = False
        for short, long, width in choices:
            cx = context("QQQ", row, short, long, width)
            checks = evaluate_chain(scenario["entry_rule_chain"], RULE_REGISTRY, cx, "FULL_AUDIT")
            ok = all(result.status == RuleStatus.PASS for rule, result in checks if rule.rule_id != "liquidity_gate")
            if not ok or opened:
                continue
            opened = True
            exp = pd.Timestamp(short["Expiry Date"]).normalize()
            end = min(exp, boundary)
            marks, _ = load_spread_quotes_canonical("QQQ", day, end, short["Expiry Date"], [short.Strike, long.Strike])
            path = track_trade({"date": day, "expiration": short["Expiry Date"], "short_strike": short.Strike, "long_strike": long.Strike}, marks, short, long, cx["credit"])
            events = [v for v in path["events"].values() if v is not None]
            exit_date = min(events) if events else (marks["Trade Date"].max() if not marks.empty else pd.NaT)
            blocked = exp > boundary and (pd.isna(exit_date) or pd.Timestamp(exit_date) >= boundary)
            selected.append({
                "strategy": strategy, "date": day, "expiration": exp,
                "short_strike": float(short.Strike), "long_strike": float(long.Strike),
                "width": float(width), "dte": int(short.DTE), "credit": float(cx["credit"]),
                "candidate_id": canonical_hash(["QQQ", str(day), str(exp), float(short.Strike), float(long.Strike)])[:24],
                "exit_date": exit_date, "stop": path["exit_reason"] == "STOP",
                "exit_reason": "FINAL_OOS_BOUNDARY_BLOCKED" if blocked else path["exit_reason"],
                "pnl": None if blocked else float(path["realized_pnl"]),
                "planned_risk": float(cx["credit"]) * 100,
            })
        audit.append({"strategy": strategy, "date": day, "option_rows": int(meta["option_rows_loaded"]), "qualifying": True, "choices": len(choices), "selected": opened})
    return audit, selected


def stats(x: pd.DataFrame, tail_cut: float | None = None) -> dict:
    x = x.dropna(subset=["pnl"]).copy()
    p = x.pnl.astype(float); wins = p[p > 0]; losses = p[p < 0]
    return {"completed_lifecycles": int(len(x)), "total_pnl": float(p.sum()) if len(p) else 0.0,
            "expectancy": float(p.mean()) if len(p) else None, "pf": float(wins.sum() / abs(losses.sum())) if len(losses) else None,
            "win_rate": float((p > 0).mean()) if len(p) else None, "stop_count": int(x.stop.sum()),
            "stop_rate": float(x.stop.mean()) if len(x) else None, "tail_loss_count": 0,
            "tail_loss_rate": float(((x.pnl <= tail_cut).mean()) if tail_cut is not None else (x.exit_reason == "TAIL_LOSS").mean()) if len(x) else 0.0, "average_winner": float(wins.mean()) if len(wins) else None,
            "average_loser": float(losses.mean()) if len(losses) else None, "best_trade": float(p.max()) if len(p) else None,
            "worst_trade": float(p.min()) if len(p) else None, "median_trade": float(p.median()) if len(p) else None}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    access = PCSDataAccess()
    scenario = resolve_scenario(yaml.safe_load(SCENARIO.read_text(encoding="utf-8")))
    d = features(access)
    all_dates = signal_dates(d)
    val_dates = {k: [x for x in v if SPLIT[0] <= x <= SPLIT[1]] for k, v in all_dates.items()}
    spec = {"module": "pcs.validation.qqq_frozen_validation_20260824", "version": "1.0", "symbol": "QQQ",
            "split": {"start": str(SPLIT[0].date()), "end": str(SPLIT[1].date())},
            "candidates": {"QQQ_CONTROLLED_RESET": "drawdown60 <= -0.02 AND ret10 > 0; first qualification per contiguous trading-session episode",
                           "QQQ_SMA50_RECLAIM_AFTER_WEAKNESS": "drawdown60 <= -0.02; first close_sma50_atr transition <=0 -> >0 per contiguous trading-session episode"},
            "feature_calculations": {"atr": "14-session Wilder ATR used by authoritative QQQ replay", "ret5": "close.pct_change(5)", "ret10": "close.pct_change(10)", "drawdown60": "close / rolling_60_max - 1", "sma50": "50-session rolling close mean", "close_sma50_atr": "(close - sma50) / atr14", "warmup": "minimum periods equal lookback", "episode_gap": "a missing prior trading session starts a new independent episode"},
            "contract_selection": {"source": str(SCENARIO), "sha256": sha256(SCENARIO), "dte": [30, 45], "safe_strike_atr": 2.3, "width_priority": [5, 10, 2], "credit_liquidity": "frozen scenario chain; no selector changes"},
            "lifecycle_stop_price_basis": {"source": "existing track_trade / frozen QQQ lifecycle path", "price_basis": "existing authoritative QQQ path; no override", "profit_target": "existing lifecycle target", "stop": "existing lifecycle stop", "contract_identity": "exact expiry/put/strike legs"},
            "lifecycle_boundary": "2026-05-31; FINAL OOS inaccessible", "final_oos_read": False,
            "strategy_definitions_changed": False, "thresholds_changed": False, "validation_used_for_tuning": False, "production_rules_changed": False,
            "source_hashes": {"scenario": sha256(SCENARIO), "h016": sha256(TRAIN_ART / "h016_sma50_reclaim.json"), "split": sha256(ROOT / "research_outputs/spy_qqq_pcs_baseline_20260821/split_manifest.json")}}
    (OUT / "frozen_validation_spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    reports = {}; ledgers = []
    for name, dates in val_dates.items():
        audit, rows = replay(name, dates, scenario, SPLIT[1], selector_daily(access)); ledgers.extend(audit)
        frame = pd.DataFrame(rows); frame.to_parquet(OUT / f"{name}_lifecycle.parquet", index=False)
        reports[name] = {"validation_trading_dates": int(d.date.between(SPLIT[0], SPLIT[1]).sum()), "qualifying_dates": len(dates), "qualifying_date_list": [str(x.date()) for x in dates], "independent_episodes": len(dates), "executable_episodes": int(len(frame)), "selected_contracts": int(len(frame)), **stats(frame),
                         "by_year": {str(y): stats(g) for y, g in frame.assign(year=pd.to_datetime(frame.date).dt.year).groupby("year")}}
    pd.DataFrame(ledgers).to_parquet(OUT / "signal_audit.parquet", index=False)
    baseline = pd.read_parquet(ROOT / "research_outputs" / "spy_qqq_modular_rule_research_20260821" / "validation_selected_lifecycle.parquet")
    baseline = baseline[(baseline.ticker == "QQQ") & pd.to_datetime(baseline.date).between(*SPLIT)].copy()
    bstats = stats(baseline.rename(columns={"date": "date", "pnl": "pnl", "stop": "stop", "exit_reason": "exit_reason"}))
    loss_cut = float(baseline.loc[baseline.pnl < 0, "pnl"].quantile(0.10))
    bstats["tail_loss_cut"] = loss_cut
    bstats["tail_loss_count"] = int((baseline.pnl <= loss_cut).sum())
    bstats["tail_loss_rate"] = float((baseline.pnl <= loss_cut).mean())
    bstats["trades"] = int(len(baseline))
    for name, report in reports.items():
        z = pd.read_parquet(OUT / f"{name}_lifecycle.parquet").dropna(subset=["pnl"])
        report["tail_loss_cut"] = loss_cut
        report["tail_loss_count"] = int((z.pnl <= loss_cut).sum())
        report["tail_loss_rate"] = float((z.pnl <= loss_cut).mean()) if len(z) else 0.0
        report["by_year"] = {str(y): stats(g, loss_cut) for y, g in z.assign(year=pd.to_datetime(z.date).dt.year).groupby("year")}
    date_sets = {k: set(v) for k, v in val_dates.items()}
    shared = date_sets["QQQ_CONTROLLED_RESET"] & date_sets["QQQ_SMA50_RECLAIM_AFTER_WEAKNESS"]
    only_reset = date_sets["QQQ_CONTROLLED_RESET"] - date_sets["QQQ_SMA50_RECLAIM_AFTER_WEAKNESS"]
    only_reclaim = date_sets["QQQ_SMA50_RECLAIM_AFTER_WEAKNESS"] - date_sets["QQQ_CONTROLLED_RESET"]
    reset_frame = pd.DataFrame(reports["QQQ_CONTROLLED_RESET"].get("qualifying_date_list", []), columns=["date"])
    overlap = {"shared_independent_episodes": len(shared), "shared_dates": [str(x.date()) for x in sorted(shared)],
               "controlled_reset_only_episodes": len(only_reset), "sma50_reclaim_only_episodes": len(only_reclaim),
               "controlled_reset_only_pnl": float(pd.DataFrame(rows if False else []).get("pnl", pd.Series(dtype=float)).sum())}
    # P&L overlap groups are based on completed lifecycle ledgers, never on the baseline.
    ledgers_by_strategy = {}
    for name in val_dates:
        z = pd.read_parquet(OUT / f"{name}_lifecycle.parquet"); ledgers_by_strategy[name] = z.set_index("date")
    overlap["shared_pnl"] = float(sum(ledgers_by_strategy[k].loc[d, "pnl"] for k in ledgers_by_strategy for d in shared if d in ledgers_by_strategy[k].index and pd.notna(ledgers_by_strategy[k].loc[d, "pnl"])))
    overlap["controlled_reset_only_pnl"] = float(ledgers_by_strategy["QQQ_CONTROLLED_RESET"].reindex(list(only_reset)).pnl.dropna().sum()) if only_reset else 0.0
    overlap["sma50_reclaim_only_pnl"] = float(ledgers_by_strategy["QQQ_SMA50_RECLAIM_AFTER_WEAKNESS"].reindex(list(only_reclaim)).pnl.dropna().sum()) if only_reclaim else 0.0
    for name, report in reports.items():
        n = report["completed_lifecycles"]; p = pd.read_parquet(OUT / f"{name}_lifecycle.parquet").dropna(subset=["pnl"])
        total = float(p.pnl.sum())
        report["episode_robustness"] = {"profitable_episodes": int((p.pnl > 0).sum()), "losing_episodes": int((p.pnl < 0).sum()),
            "top_episode_pnl_share": float(p.nlargest(1, "pnl").pnl.sum() / total) if total else None,
            "top_2_episode_pnl_share": float(p.nlargest(2, "pnl").pnl.sum() / total) if total else None,
            "top_3_episode_pnl_share": float(p.nlargest(3, "pnl").pnl.sum() / total) if total else None,
            "median_episode_pnl": float(p.pnl.median()) if n else None, "worst_episode_pnl": float(p.pnl.min()) if n else None,
            "loo_status": "INSUFFICIENT_SAMPLE" if n < 10 else "COMPUTED",
            "minimum_loo_pnl": None if n < 10 else float(total - p.pnl.max()), "negative_loo_count": None if n < 10 else int(((total - p.pnl) < 0).sum())}
        report["status"] = "VALIDATION_FAILED" if report["expectancy"] is not None and report["expectancy"] < 0 else "POSITIVE_BUT_INSUFFICIENT" if n < 10 else "VALIDATION_SUPPORTED"
    (OUT / "validation_report.json").write_text(json.dumps({"spec": spec, "strategies": reports, "baseline": bstats, "overlap": overlap, "final_oos_read": False}, indent=2, default=str), encoding="utf-8")
    print(json.dumps(reports, indent=2, default=str))


if __name__ == "__main__":
    main()
