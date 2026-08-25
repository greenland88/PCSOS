"""PIT historical adapter for the canonical production trend context."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.entry_candidate_universe import build_historical_setup_context, _daily

def _strict_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"true", "1", "yes"}:
            return True
        if value in {"false", "0", "no", ""}:
            return False
    return bool(value)


def _jsonable(value: Any):
    if hasattr(value, "__dict__"):
        return {k: _jsonable(v) for k, v in value.__dict__.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value


class HistoricalTrendContextProvider:
    def __init__(self, ticker: str, daily_root: str | Path = "data/raw/daily_forward_adjusted", benchmark: str = "QQQ"):
        self.ticker = ticker; self.benchmark = benchmark
        if Path(daily_root) == Path("data/raw/daily_forward_adjusted"):
            access = PCSDataAccess()
            self.stock = access.read_prices(ticker)
            self.bench = access.read_prices(benchmark)
        else:
            root = Path(daily_root)
            self.stock = _daily(root / f"{ticker}_daily_qfq.csv")
            self.bench = _daily(root / f"{benchmark}_daily_qfq.csv")
        self.cache: dict[pd.Timestamp, dict[str, Any]] = {}

    def __call__(self, row: dict[str, Any]) -> dict[str, Any]:
        day = pd.Timestamp(row["date"]).normalize()
        if day not in self.cache:
            self.cache[day] = build_historical_setup_context(self.stock, self.bench, day, self.ticker, self.benchmark)
        return self.cache[day]

    def serialized(self, row: dict[str, Any]) -> dict[str, Any]:
        ctx = self(row)
        return {"candidate_id": row["candidate_id"], "ticker": self.ticker, "decision_date": row["date"],
                "context_available": _strict_bool(ctx.get("available", False)),
                "trend_snapshot": _jsonable(ctx.get("snapshot")),
                "trend_interpretation": _jsonable(ctx.get("interpretation")),
                "trend_score_result": _jsonable(ctx.get("trend_score")),
                "reason_codes": ctx.get("reason_codes", []), "warnings": ctx.get("warnings", []),
                "producer": "pcs.research.entry_candidate_universe.build_historical_setup_context",
                "pit_asof": row["date"], "pit": True}
