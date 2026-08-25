"""Ticker-independent PCS strategy definitions.

This module contains predicates only. It deliberately owns no data access,
contract selection, lifecycle, or production configuration.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Callable
import pandas as pd

@dataclass(frozen=True)
class Evaluation:
    strategy_id: str
    ticker: str
    date: Any
    status: str
    reason: str
    reason_codes: tuple[str, ...]
    feature_values: dict[str, Any]

@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    source: str
    exact_rule: str
    features: tuple[str, ...]
    entry_rule: str
    episode_rule: str
    _predicate: Callable[[dict[str, Any]], bool]

    def evaluate(self, ticker: str, date: Any, pit_features: dict[str, Any]) -> Evaluation:
        values = {k: pit_features.get(k) for k in self.features}
        missing = [k for k, v in values.items() if v is None or pd.isna(v)]
        if missing:
            return Evaluation(self.strategy_id, str(ticker).upper(), date, "NO_QUALIFY", "missing PIT feature", ("PIT_FEATURE_MISSING",), values)
        ok = bool(self._predicate(values))
        return Evaluation(self.strategy_id, str(ticker).upper(), date, "QUALIFY" if ok else "NO_QUALIFY", "exact rule satisfied" if ok else "exact rule not satisfied", ("QUALIFY" if ok else "RULE_NOT_SATISFIED",), values)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self); d.pop("_predicate", None); return d

def _gt(a, b): return float(a) > b

STRATEGIES: dict[str, StrategySpec] = {
    "PCS_NVDA_TREND_CONTINUATION_V1": StrategySpec("PCS_NVDA_TREND_CONTINUATION_V1", "V2_H010", "close > PIT_SMA200 AND volume_relative_to_20d_mean > 1 AND ret5 > 0", ("close", "sma200", "volume_relative_to_20d_mean", "ret5"), "first qualifying date per independent episode", "contiguous qualifying dates; gap starts a new episode", lambda x: x["close"] > x["sma200"] and x["volume_relative_to_20d_mean"] > 1 and x["ret5"] > 0),
    "PCS_CONSTRUCTIVE_RECOVERY_V1": StrategySpec("PCS_CONSTRUCTIVE_RECOVERY_V1", "V2_H027", "close > PIT_SMA200 AND ret20 < 0 AND ret5 > 0", ("close", "sma200", "ret20", "ret5"), "first qualifying date per independent episode", "contiguous qualifying dates; gap starts a new episode", lambda x: x["close"] > x["sma200"] and x["ret20"] < 0 and x["ret5"] > 0),
    "PCS_CONTROLLED_RESET_V1": StrategySpec("PCS_CONTROLLED_RESET_V1", "QQQ CONTROLLED_RESET", "drawdown60 <= -0.02 AND ret10 > 0", ("drawdown60", "ret10"), "first qualifying date per independent episode", "contiguous qualifying dates; gap starts a new episode", lambda x: x["drawdown60"] <= -.02 and x["ret10"] > 0),
    "PCS_RESET_RECOVERY_V1": StrategySpec("PCS_RESET_RECOVERY_V1", "QQQ recovery family", "drawdown60 <= -0.02 AND ret10 > 0 AND ret5 > 0", ("drawdown60", "ret10", "ret5"), "first confirmation per independent episode", "controlled-reset episode; first confirmation", lambda x: x["drawdown60"] <= -.02 and x["ret10"] > 0 and x["ret5"] > 0),
    "PCS_SMA50_RECLAIM_V1": StrategySpec("PCS_SMA50_RECLAIM_V1", "QQQ H016", "drawdown60 <= -0.02 AND prior_close_sma50_atr <= 0 AND close_sma50_atr > 0", ("drawdown60", "prior_close_sma50_atr", "close_sma50_atr"), "first reclaim per independent episode", "authoritative H016 weakness/transition episode", lambda x: x["drawdown60"] <= -.02 and x["prior_close_sma50_atr"] <= 0 and x["close_sma50_atr"] > 0),
}

def get_strategy(strategy_id: str) -> StrategySpec:
    try: return STRATEGIES[strategy_id]
    except KeyError as e: raise KeyError(f"UNKNOWN_STRATEGY:{strategy_id}") from e

def evaluate(strategy_id: str, ticker: str, date: Any, pit_features: dict[str, Any]) -> Evaluation:
    return get_strategy(strategy_id).evaluate(ticker, date, pit_features)
