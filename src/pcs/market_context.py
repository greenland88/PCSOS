"""Canonical point-in-time market context composition."""
from __future__ import annotations
from datetime import date
from typing import Any
import pandas as pd
from pydantic import BaseModel
from pcs.data.access import PCSDataAccess
from pcs.models.market import MarketState
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.snapshot import build_trend_snapshot
from pcs.trend.interpretation import interpret_trend
from pcs.trend.scoring import score_trend
from pcs.engine.decision_engine import load_rules
from pcs.regime.market_regime import MarketRegimeEngine
from pcs.trend.indicators import calculate_base_indicators
from pcs.data.correctness_gate import DataCorrectnessError, validate_price_input

class MarketContext(BaseModel):
    symbol: str
    as_of: str
    underlying_price: float
    sma20: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    atr14: float | None = None
    adx: float | None = None
    rsi: float | None = None
    trend: str | None = None
    trend_score: float | None = None
    structural_trend: str | None = None
    short_term_phase: str | None = None
    feature_max_date: str | None = None
    timing_action: str | None = None
    timing_reason_codes: list[str] = []
    regime: str
    support: float | None = None
    event_risk: int = 0
    data_timestamp: str
    market_state: MarketState
    snapshot: Any = None
    interpretation: Any = None
    score_result: Any = None

def build_market_context(symbol: str, as_of: date | str, *, data_access: PCSDataAccess,
                         event_risk: int = 0, rules: dict | None = None,
                         daily_frame: pd.DataFrame | None = None,
                         benchmark_frame: pd.DataFrame | None = None,
                         spy_frame: pd.DataFrame | None = None,
                         soxx_frame: pd.DataFrame | None = None,
                         verified_handles: dict[str, Any] | None = None,
                         mode: str = "RESEARCH_COMPATIBILITY") -> MarketContext:
    s = str(symbol).strip().upper(); cutoff = pd.Timestamp(as_of).normalize()
    if mode == "FORMAL":
        if any(x is None for x in (daily_frame, benchmark_frame, spy_frame, soxx_frame)):
            raise DataCorrectnessError("MARKET_CONTEXT_INPUT_NOT_PINNED")
        handles = verified_handles or {}
        if not all(key in handles for key in (s, "QQQ", "SPY", "SOXX")):
            raise DataCorrectnessError("MARKET_CONTEXT_INPUT_NOT_PINNED")
        frames = (daily_frame, benchmark_frame, spy_frame, soxx_frame)
        frame_symbols = (s, "QQQ", "SPY", "SOXX")
        for frame, frame_symbol in zip(frames, frame_symbols):
            if "date" not in frame or "symbol" not in frame:
                raise DataCorrectnessError("SCHEMA_MISMATCH")
            validate_price_input(frame, handles[frame_symbol], frame_symbol, as_of=cutoff)
            dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
            if dates.duplicated().any():
                raise DataCorrectnessError("DUPLICATE_CANONICAL_PRICE_KEY")
            if dates.max() > cutoff:
                raise DataCorrectnessError("FEATURE_ASOF_VIOLATION")
        if str(benchmark_frame["symbol"].iloc[0]).upper() != "QQQ":
            raise DataCorrectnessError("SYMBOL_MISMATCH")
        if str(spy_frame["symbol"].iloc[0]).upper() != "SPY" or str(soxx_frame["symbol"].iloc[0]).upper() != "SOXX":
            raise DataCorrectnessError("SYMBOL_MISMATCH")
    elif mode != "RESEARCH_COMPATIBILITY":
        raise ValueError("UNKNOWN_MARKET_CONTEXT_MODE")
    # A runner may supply a generation-pinned frame admitted by readiness.
    # Do not re-resolve the target ticker after the readiness boundary.
    daily = daily_frame.copy() if daily_frame is not None else data_access.read_prices(s, end_date=cutoff)
    if daily.empty: raise ValueError(f"DAILY_DATA_EMPTY:{s}:{cutoff.date()}")
    if mode == "FORMAL" and daily["date"].duplicated().any():
        raise DataCorrectnessError("DUPLICATE_CANONICAL_PRICE_KEY")
    daily = daily.sort_values("date").reset_index(drop=True)
    benchmark = (benchmark_frame.copy() if benchmark_frame is not None else
                 data_access.read_prices("QQQ", end_date=cutoff)).sort_values("date").reset_index(drop=True)
    if benchmark.empty: raise ValueError(f"BENCHMARK_DATA_EMPTY:QQQ:{cutoff.date()}")
    snapshot = build_trend_snapshot(daily, benchmark, as_of_date=cutoff,
                                    symbol=s, benchmark="QQQ", config=TrendIndicatorConfig())
    interpretation = interpret_trend(snapshot)
    score = score_trend(snapshot, interpretation)
    ind = calculate_base_indicators(daily, TrendIndicatorConfig())
    row = ind.iloc[-1]; price = float(daily.iloc[-1].close)
    def _v(name):
        value = row.get(name); return None if pd.isna(value) else float(value)
    def _above(ticker, period, supplied=None):
        frame = (supplied if supplied is not None else data_access.read_prices(ticker, end_date=cutoff)).sort_values("date")
        close = pd.to_numeric(frame.close); return bool(close.iloc[-1] > close.rolling(period, min_periods=period).mean().iloc[-1])
    spy = _above("SPY", 50, spy_frame if mode == "FORMAL" else None); qqq50 = _above("QQQ", 50, benchmark if mode == "FORMAL" else None)
    qqq = benchmark if mode == "FORMAL" else data_access.read_prices("QQQ", end_date=cutoff).sort_values("date"); qclose = pd.to_numeric(qqq.close)
    qqq20 = bool(qclose.iloc[-1] > qclose.rolling(20, min_periods=20).mean().iloc[-1]); qqq200 = bool(qclose.iloc[-1] > qclose.rolling(200, min_periods=200).mean().iloc[-1])
    state = MarketState(qqq_above_20dma=qqq20, qqq_above_50dma=qqq50, qqq_above_200dma=qqq200,
                        spy_above_50dma=spy, soxx_above_50dma=_above("SOXX", 50, soxx_frame if mode == "FORMAL" else None), breadth_positive=spy and qqq50)
    regime, _, _ = MarketRegimeEngine((rules or load_rules())).classify(state)
    ms_engine = snapshot.market_structure_engine
    timing_action = "WAIT" if ms_engine and ms_engine.short_term_phase in {"RECLAIM_DAY_1", "RECLAIM_UNCONFIRMED", "FAILED_FOLLOW_THROUGH", "BREAKOUT_REJECTED", "UPTREND_EXHAUSTION", "DISTRIBUTION"} else "ENTRY_READY" if ms_engine and ms_engine.short_term_phase in {"CONTINUATION", "HEALTHY_PULLBACK", "RECLAIM_CONFIRMED", "BREAKOUT_CONFIRMED"} else "DATA_BLOCKED"
    return MarketContext(symbol=s, as_of=cutoff.date().isoformat(), underlying_price=price, sma20=_v("sma20"), sma50=_v("sma50"), sma200=_v("sma200"), atr14=_v("atr14"), adx=_v("adx14"), rsi=_v("rsi14"), trend=interpretation.trend_direction, trend_score=score.trend_score, structural_trend=ms_engine.structural_trend if ms_engine else None, short_term_phase=ms_engine.short_term_phase if ms_engine else None, feature_max_date=str(ms_engine.feature_max_date.date()) if ms_engine and hasattr(ms_engine.feature_max_date, "date") else str(cutoff.date()), timing_action=timing_action, timing_reason_codes=list(ms_engine.reasons) if ms_engine else ["MARKET_STRUCTURE_ENGINE_UNAVAILABLE"], regime=regime.value, support=snapshot.support.nearest_support, event_risk=event_risk, data_timestamp=str(pd.to_datetime(daily.date).max().date()), market_state=state, snapshot=snapshot, interpretation=interpretation, score_result=score)
