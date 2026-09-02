"""U1 ticker-pool runner: raw universe, static data readiness, and daily timing."""
from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Literal, Sequence
import uuid

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.trend.snapshot import build_trend_snapshot
from .models import (EligibilityStatus, FinalAction, OptionsStatus, PoolRunSnapshot,
                     PoolScanResult, TickerScanResult, TimingStatus)
from .registry import UniverseSpec, evaluate_static_eligibility


def _as_of(value) -> str:
    if value == "latest":
        return datetime.now(timezone.utc).isoformat()
    return pd.Timestamp(value).isoformat()


def run_pcs_pool(*, universe_id: str | None = None, symbols: Sequence[str] | None = None,
                 as_of: datetime | str = "latest",
                 mode: Literal["PREMARKET", "INTRADAY", "EOD"],
                 strategies: Sequence[str] | None = None,
                 event_policy: Literal["HOLD_TO_EXPIRY", "PLANNED_EARLY_EXIT"] = "HOLD_TO_EXPIRY",
                 planned_exit_before_event_sessions: int | None = None,
                 max_workers: int = 8, output_directory=None,
                 data_access: PCSDataAccess | None = None,
                 benchmark_symbol: str = "QQQ", options_reader=None,
                 option_rules=None) -> PoolScanResult:
    """Run the non-mutating U1 funnel.

    Options, events, and portfolio stages intentionally remain not evaluated
    until their adapters are integrated; no options chain is read here.
    """
    if mode not in {"PREMARKET", "INTRADAY", "EOD"}:
        raise ValueError("mode must be PREMARKET, INTRADAY, or EOD")
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    if symbols is None:
        spec = UniverseSpec.from_config("config/market_universe.yaml")
    else:
        spec = UniverseSpec.from_symbols(symbols, universe_id=universe_id or "explicit")
    run_id = uuid.uuid4().hex
    asof = _as_of(as_of)
    access = data_access or PCSDataAccess()
    benchmark = None
    try:
        benchmark = access.read_prices(benchmark_symbol, end_date=asof)
    except Exception:
        benchmark = None
    snapshot = PoolRunSnapshot(run_id, asof, mode, None, f"{spec.universe_id}:{spec.version}",
                               benchmark_handles={benchmark_symbol: "PINNED" if benchmark is not None else "UNAVAILABLE"})
    results = []
    for symbol in spec.symbols:
        started = perf_counter()
        entry = evaluate_static_eligibility(symbol)
        if entry.status != EligibilityStatus.PCS_ELIGIBLE:
            results.append(TickerScanResult(symbol, run_id, asof, entry.status,
                final_action=FinalAction.DATA_FAILED, reason_codes=entry.reason_codes,
                latency_ms=(perf_counter()-started)*1000))
            continue
        try:
            daily = access.read_prices(symbol, end_date=asof)
            if benchmark is None or daily.empty:
                raise ValueError("BENCHMARK_OR_DAILY_DATA_UNAVAILABLE")
            trend = build_trend_snapshot(daily, benchmark, as_of_date=asof, symbol=symbol, benchmark=benchmark_symbol)
            engine = trend.market_structure_engine
            phase = str(getattr(engine, "short_term_phase", ""))
            if phase in {"RECLAIM_CONFIRMED", "HEALTHY_PULLBACK", "BREAKOUT_CONFIRMED"}:
                timing = TimingStatus.TIMING_ENTRY_READY
                action = FinalAction.WAIT  # options/event/risk are not yet evaluated
            elif phase:
                timing = TimingStatus.WATCH
                action = FinalAction.WATCH
            else:
                timing = TimingStatus.WAIT
                action = FinalAction.WAIT
            feature_date = getattr(engine, "feature_max_date", None)
            options_status = OptionsStatus.NOT_EVALUATED
            option_reasons = ()
            if timing == TimingStatus.TIMING_ENTRY_READY and options_reader is not None:
                from .options import shortlist_spreads
                chain = options_reader(symbol, pd.Timestamp(feature_date).normalize())
                close = float(daily.loc[pd.to_datetime(daily.date).idxmax(), "close"])
                atr = float(getattr(trend.support, "current_atr", 0) or 0)
                candidates = shortlist_spreads(symbol, feature_date, close, atr, chain,
                                                rules=option_rules or {})
                options_status = OptionsStatus.PASS if candidates else OptionsStatus.REJECT
                option_reasons = ("OPTIONS_SHORTLIST_PASS" if candidates else "NO_QUALIFYING_SPREAD",)
                action = FinalAction.WAIT
            results.append(TickerScanResult(symbol, run_id, asof, entry.status, timing,
                options_status, final_action=action,
                reason_codes=tuple(getattr(engine, "reason_codes", ())) + option_reasons or ("TIMING_EVALUATED",),
                feature_max_date=str(feature_date), latency_ms=(perf_counter()-started)*1000))
        except Exception as exc:
            results.append(TickerScanResult(symbol, run_id, asof, EligibilityStatus.DATA_BLOCKED,
                final_action=FinalAction.DATA_FAILED, reason_codes=("DAILY_TIMING_FAILED", type(exc).__name__),
                latency_ms=(perf_counter()-started)*1000))
    summary = {"raw_count": len(spec.symbols),
               "pcs_eligible_count": sum(r.eligibility_status == EligibilityStatus.PCS_ELIGIBLE for r in results),
               "timing_entry_ready_count": sum(r.timing_status == TimingStatus.TIMING_ENTRY_READY for r in results),
               "options_check_count": 0, "missing_ticker_decisions": len(spec.symbols)-len(results)}
    result = PoolScanResult(snapshot, tuple(results), summary)
    if output_directory is not None:
        from .artifacts import persist_pool_artifacts
        persist_pool_artifacts(result, output_directory)
    return result


__all__ = ["run_pcs_pool"]
