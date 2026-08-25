"""Generic research-only exact strategy transfer runner."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import json
import pandas as pd
from pcs.data.access import PCSDataAccess, DataAccessError, DataQualityError
from pcs.strategies.research_templates.catalog import get_strategy

@dataclass(frozen=True)
class TransferRequest:
    strategy_id: str
    ticker: str
    train_start: str
    train_end: str

def _features(d: pd.DataFrame) -> pd.DataFrame:
    x = d.sort_values("date").copy(); c = pd.to_numeric(x["close"])
    x["sma200"] = c.rolling(200, min_periods=200).mean(); x["sma50"] = c.rolling(50, min_periods=50).mean()
    x["ret5"] = c.pct_change(5); x["ret10"] = c.pct_change(10); x["ret20"] = c.pct_change(20)
    x["drawdown60"] = c / c.rolling(60, min_periods=60).max() - 1
    if "volume" in x: x["volume_relative_to_20d_mean"] = x.volume / x.volume.rolling(20, min_periods=20).mean()
    else: x["volume_relative_to_20d_mean"] = pd.NA
    atr = (x.high - x.low).rolling(14, min_periods=14).mean()
    x["close_sma50_atr"] = (c - x.sma50) / atr
    x["prior_close_sma50_atr"] = x.close_sma50_atr.shift(1)
    return x

def run_transfer(request: TransferRequest, *, data_access: PCSDataAccess | None = None, output_dir: str | Path | None = None) -> dict[str, Any]:
    spec = get_strategy(request.strategy_id); ticker = request.ticker.upper(); access = data_access or PCSDataAccess()
    try:
        requested_start = pd.Timestamp(request.train_start)
        warmup_start = requested_start - pd.Timedelta(days=320)
        daily = access.read_prices(ticker, warmup_start, request.train_end)
        access.validate_schema(daily, "daily"); access.validate_coverage(daily, ticker, request.train_start, request.train_end, "date")
        if daily.empty or daily.date.duplicated().any(): raise DataQualityError("daily duplicate keys or empty coverage")
        # Options validation is intentionally a readiness gate; execution engines
        # remain delegated to the canonical selector/lifecycle in full replays.
        options = access.read_quotes(ticker, request.train_start, request.train_end)
        access.validate_schema(options, "options")
    except (OSError, ValueError, DataAccessError, DataQualityError) as exc:
        return {"module":"pcs.research.strategy_transfer_runner","status":"DATA_BLOCKED","reason_codes":["CANONICAL_DATA_INVALID"],"ticker":ticker,"strategy_id":request.strategy_id,"error":str(exc)}
    frame = _features(daily); frame = frame[frame.date >= requested_start].copy(); dates=[]; evaluations=[]
    for _, row in frame.iterrows():
        ev = spec.evaluate(ticker, row.date, row.to_dict()); evaluations.append(asdict(ev))
        if ev.status == "QUALIFY": dates.append(pd.Timestamp(row.date))
    qualifying = pd.Series(sorted(set(dates)))
    episodes=[]
    if len(qualifying):
        sessions = pd.DatetimeIndex(frame.date).normalize()
        positions = {d: i for i, d in enumerate(sessions)}
        breaks = qualifying.map(lambda d: positions.get(pd.Timestamp(d).normalize(), -999)).diff().fillna(999).ne(1).cumsum()
        episodes = [{"episode_id":int(i),"qualifying_dates":[d.date().isoformat() for d in g]} for i,g in qualifying.groupby(breaks)]
    result = {"module":"pcs.research.strategy_transfer_runner","version":"v1","status":"COMPLETED_DESCRIPTIVE","strategy_id":request.strategy_id,"ticker":ticker,"train_start":request.train_start,"train_end":request.train_end,"qualifying_dates":[d.date().isoformat() for d in qualifying],"independent_episodes":episodes,"executable_episodes":[],"trades":[],"performance":{},"reason_codes":["EXACT_TRANSFER","PIT_FEATURES","CANONICAL_DATA_VALIDATED","CONTRACT_LIFECYCLE_DELEGATED"]}
    if output_dir:
        out=Path(output_dir); out.mkdir(parents=True, exist_ok=True); (out/"transfer_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

__all__ = ["TransferRequest", "run_transfer"]
