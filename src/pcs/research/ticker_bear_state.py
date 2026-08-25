"""PIT-safe, research-only single-ticker bear-state classification."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

import pandas as pd

from pcs.data.daily_provider import DailyDataProvider, normalize_daily_frame


class TickerBearState(StrEnum):
    NORMAL = "NORMAL"
    WEAK_BEAR = "WEAK_BEAR"
    BEAR_CONFIRMED = "BEAR_CONFIRMED"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


CALCULATION_VERSION = "ticker-bear-state-v1"
PRODUCER = "pcs.research.ticker_bear_state"
REQUIRED_HISTORY_DAYS = 252
CONFIRMATION_DAYS = 5


@dataclass(frozen=True)
class TickerBearStateResult:
    symbol: str
    as_of: str | None
    data_timestamp: str | None
    calculation_version: str
    producer: str
    status: str
    reason_codes: tuple[str, ...]
    run_id: str
    request_id: str
    records: list[dict[str, Any]]


def calculate_ticker_bear_states(ohlcv: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Classify each daily bar using only that row and preceding daily bars."""
    out = normalize_daily_frame(ohlcv)
    close = out["close"].astype(float)
    out["sma50"] = close.rolling(50, min_periods=50).mean()
    out["sma200"] = close.rolling(200, min_periods=200).mean()
    out["high_52w"] = out["high"].astype(float).rolling(REQUIRED_HISTORY_DAYS, min_periods=REQUIRED_HISTORY_DAYS).max()
    out["drawdown_52w"] = (out["high_52w"] - close) / out["high_52w"]
    sufficient = out[["sma50", "sma200", "high_52w"]].notna().all(axis=1)
    out["close_below_sma200"] = pd.Series(pd.NA, index=out.index, dtype="boolean")
    out["sma50_below_sma200"] = pd.Series(pd.NA, index=out.index, dtype="boolean")
    out["drawdown_ge_20pct"] = pd.Series(pd.NA, index=out.index, dtype="boolean")
    out.loc[sufficient, "close_below_sma200"] = close.loc[sufficient] < out.loc[sufficient, "sma200"]
    out.loc[sufficient, "sma50_below_sma200"] = out.loc[sufficient, "sma50"] < out.loc[sufficient, "sma200"]
    out.loc[sufficient, "drawdown_ge_20pct"] = out.loc[sufficient, "drawdown_52w"] >= 0.20
    full_bear = sufficient & out.close_below_sma200.fillna(False) & out.sma50_below_sma200.fillna(False) & out.drawdown_ge_20pct.fillna(False)
    groups = (~full_bear).cumsum()
    consecutive = full_bear.astype(int).groupby(groups).cumsum()
    out["consecutive_full_bear_days"] = consecutive.where(sufficient, pd.NA).astype("Int64")
    condition_count = (out[["close_below_sma200", "sma50_below_sma200", "drawdown_ge_20pct"]].fillna(False).sum(axis=1))
    state = pd.Series(TickerBearState.INSUFFICIENT_HISTORY.value, index=out.index, dtype="string")
    state.loc[sufficient & condition_count.lt(2)] = TickerBearState.NORMAL.value
    state.loc[sufficient & condition_count.eq(2)] = TickerBearState.WEAK_BEAR.value
    state.loc[sufficient & condition_count.eq(3) & consecutive.ge(CONFIRMATION_DAYS)] = TickerBearState.BEAR_CONFIRMED.value
    # A full-bear run is normal until confirmation is complete; never promote early.
    state.loc[sufficient & condition_count.eq(3) & consecutive.lt(CONFIRMATION_DAYS)] = TickerBearState.WEAK_BEAR.value
    out["ticker_bear_state"] = state
    out["ticker"] = symbol.upper()
    out["producer"] = PRODUCER
    out["calculation_version"] = CALCULATION_VERSION
    out["reason_code"] = ""
    out.loc[~sufficient, "reason_code"] = "INSUFFICIENT_HISTORY"
    return out[["ticker", "date", "close", "sma50", "sma200", "high_52w", "drawdown_52w", "close_below_sma200", "sma50_below_sma200", "drawdown_ge_20pct", "consecutive_full_bear_days", "ticker_bear_state", "producer", "calculation_version", "reason_code"]]


def build_ticker_bear_state_history(symbol: str, as_of_date=None, provider: DailyDataProvider | None = None, request_id: str | None = None) -> TickerBearStateResult:
    provider = provider or DailyDataProvider()
    bars = provider.build_daily_series(symbol, as_of_date=as_of_date)
    states = calculate_ticker_bear_states(bars, symbol)
    last = states.iloc[-1] if len(states) else None
    return TickerBearStateResult(
        symbol=symbol.upper(), as_of=str(last.date.date()) if last is not None else None,
        data_timestamp=str(last.date.date()) if last is not None else None,
        calculation_version=CALCULATION_VERSION, producer=PRODUCER,
        status="READY" if last is not None and last.ticker_bear_state != TickerBearState.INSUFFICIENT_HISTORY else "INSUFFICIENT_HISTORY",
        reason_codes=tuple(() if last is not None and last.ticker_bear_state != TickerBearState.INSUFFICIENT_HISTORY else ("INSUFFICIENT_HISTORY",)),
        run_id=f"ticker-bear-state-{uuid4().hex}", request_id=request_id or f"ticker-bear-state-request-{uuid4().hex}",
        records=states.to_dict("records"),
    )
