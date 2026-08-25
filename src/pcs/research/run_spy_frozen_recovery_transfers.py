"""Run the three frozen QQQ recovery predicates as isolated SPY replays."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.research.runner import ResearchRunner
from pcs.research.research_framework import ResearchMode, validate_population_routing, validate_rule_set

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config" / "research"


def features(daily: pd.DataFrame) -> pd.DataFrame:
    x = daily.sort_values("date").copy()
    c = x["close"].astype(float)
    x["sma50"] = c.rolling(50, min_periods=50).mean()
    x["atr"] = (x["high"].astype(float) - x["low"].astype(float)).rolling(14, min_periods=14).mean()
    x["drawdown60"] = c / c.rolling(60, min_periods=60).max() - 1
    x["ret5"] = c.pct_change(5)
    x["ret10"] = c.pct_change(10)
    x["close_sma50_atr"] = (c - x["sma50"]) / x["atr"]
    x["prior_close_sma50_atr"] = x["close_sma50_atr"].shift(1)
    return x


def first_per_episode(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    x = frame.sort_values("date").copy()
    x["episode"] = (x["date"].diff().dt.days.fillna(999) > 4).cumsum()
    return [d.date().isoformat() for d in x.groupby("episode", as_index=False).first()["date"]]


def signal_dates(kind: str, frame: pd.DataFrame) -> list[str]:
    reset = frame[(frame.drawdown60 <= -0.02) & (frame.ret10 > 0)]
    if kind == "controlled_reset":
        return first_per_episode(reset)
    if kind == "recovery_stabilization":
        # Preserve the frozen precursor episode boundary, then select its first
        # date satisfying the frozen confirmation predicate.
        if reset.empty:
            return []
        reset = reset.sort_values("date").copy()
        reset["episode"] = (reset.date.diff().dt.days.fillna(999) > 4).cumsum()
        out = []
        for _, group in reset.groupby("episode"):
            hit = group[group.ret5 > 0]
            if not hit.empty:
                out.append(hit.iloc[0].date().date().isoformat())
        return out
    if kind == "sma50_reclaim":
        return first_per_episode(frame[(frame.drawdown60 <= -0.02) &
                                       (frame.prior_close_sma50_atr <= 0) &
                                       (frame.close_sma50_atr > 0)])
    raise ValueError(kind)


def run_one(kind: str, config_name: str, research_id: str) -> dict:
    access = PCSDataAccess()
    daily = access.read_prices("SPY", "2019-01-01", "2025-12-31")
    daily.date = pd.to_datetime(daily.date).dt.normalize()
    f = features(daily)
    f = f[f.date.between("2020-01-01", "2025-12-31")]
    dates = signal_dates(kind, f)
    base = ResearchRunner.from_path(CONFIG / config_name)
    spec = replace(
        base.spec,
        research_id=research_id,
        research_mode=ResearchMode.CURRENT_STRATEGY_REPLAY,
        population_source={"type": "ticker_daily_calendar", "frozen": False, "point_in_time": True},
        signal_definition={"track_a_execution_only": True, "creates_new_entry_dates": True,
                           "frozen_predicate": kind, "execution_dates": dates},
        rules={"dte_min": 30, "dte_max": 45, "safe_strike_atr": 2.3,
               "allowed_widths": [5, 10, 2], "width_mode": "ALL",
               "min_credit_width_ratio": 0.10, "trend_gate": False,
               "pullback_gate": False, "support_gate": False, "regime_gate": False,
               "event_gate": True, "liquidity_gate": True, "predictability_gate": False},
    )
    runner = ResearchRunner(validate_rule_set(validate_population_routing(spec)))
    result = runner.execute_research_replay(data_access=access)
    result["frozen_signal_dates"] = dates
    result["frozen_predicate"] = kind
    out = ROOT / "research_outputs" / research_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "frozen_transfer_metadata.json").write_text(json.dumps({"kind": kind, "signal_dates": dates,
        "thresholds_changed": False, "lifecycle_changed": False, "production_rules_changed": False,
        "final_oos_read": False}, indent=2), encoding="utf-8")
    return result


def main() -> None:
    jobs = [
        ("controlled_reset", "spy_frozen_controlled_reset_transfer.yaml", "spy_frozen_controlled_reset_replay"),
        ("recovery_stabilization", "spy_frozen_recovery_stabilization_transfer.yaml", "spy_frozen_recovery_stabilization_replay"),
        ("sma50_reclaim", "spy_frozen_sma50_reclaim_transfer.yaml", "spy_frozen_sma50_reclaim_replay"),
    ]
    results = {kind: run_one(kind, config, research_id) for kind, config, research_id in jobs}
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
