"""U1 ticker-pool runner: raw universe, static data readiness, and daily timing."""
from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Literal, Sequence
import uuid

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.strategy_readiness import resolve_active_verified_daily_handle
from pcs.data.control_plane import MarketDataRequirements, ensure_market_data
from pcs.trend.snapshot import build_trend_snapshot
from .models import (EligibilityStatus, FinalAction, OptionsStatus, PoolRunSnapshot,
                     PoolScanResult, TickerScanResult, TimingStatus)
from .registry import UniverseSpec, evaluate_static_eligibility
from .concurrency import run_symbol_workers
from .modes import completed_daily_cutoff


def _adopt_existing_daily_canonical(symbol: str, access: PCSDataAccess) -> None:
    """Adopt only the exact complete canonical rows for one ticker."""
    from pcs.data.canonical_generations import adopt_legacy_canonical_generation
    manifest = access._read_manifest(access.manifest_path)
    rows = manifest[(manifest.dataset.astype(str) == "daily") &
                    manifest.symbol.astype(str).str.upper().eq(str(symbol).upper())]
    if rows.empty or rows.active_generation.notna().any():
        return
    for _, row in rows.iterrows():
        path = str(row.get("parquet_path") or "").strip()
        if not path:
            return
        import hashlib
        from pathlib import Path
        file_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        adopt_legacy_canonical_generation(dataset="daily", symbol=symbol,
            legacy_manifest=row.to_dict(), expected_file_hash=file_hash,
            adoption_reason="AUTO_ADOPT_EXISTING_CANONICAL", data_access=access)


def _evaluate_symbol(symbol, *, run_id, asof, access, benchmark, benchmark_symbol,
                     options_reader, option_rules, daily_asof=None, static_metadata_reader=None,
                     daily_handle_resolver=None, auto_prepare_data=True,
                     refresh_policy="INCREMENTAL_IF_NEEDED"):
    started = perf_counter()
    metadata = static_metadata_reader(symbol) if static_metadata_reader is not None else None
    entry = evaluate_static_eligibility(symbol, metadata)
    if entry.status != EligibilityStatus.PCS_ELIGIBLE:
        return TickerScanResult(symbol, run_id, asof, entry.status,
            final_action=FinalAction.DATA_FAILED, reason_codes=entry.reason_codes,
            latency_ms=(perf_counter()-started)*1000)
    try:
        resolver = daily_handle_resolver or resolve_active_verified_daily_handle
        try:
            handle = resolver(symbol, daily_asof or asof, 200, data_access=access)
        except Exception:
            if not auto_prepare_data or daily_handle_resolver is not None:
                raise
            day = pd.Timestamp(daily_asof or asof).normalize()
            prep = MarketDataRequirements(symbol=symbol, required_start=str((day - pd.Timedelta(days=420)).date()),
                                          required_end=str(day.date()), datasets=("daily",),
                                          decision_as_of=str(day.date()), required_history_rows=200)
            prepared = ensure_market_data(symbol, prep, access=access)
            if getattr(prepared, "status", "") == "ALREADY_COMPLETE":
                _adopt_existing_daily_canonical(symbol, access)
            handle = resolver(symbol, daily_asof or asof, 200, data_access=access)
        daily = access.read_verified_dataset(handle, end_date=daily_asof or asof,
                                             required_warmup_rows=200)
        if benchmark is None or daily.empty:
            raise ValueError("BENCHMARK_OR_DAILY_DATA_UNAVAILABLE")
        # Each worker receives an independent immutable snapshot boundary;
        # trend helpers may construct intermediate columns internally.
        trend = build_trend_snapshot(daily.copy(deep=True), benchmark.copy(deep=True),
                                     as_of_date=asof, symbol=symbol, benchmark=benchmark_symbol)
        engine = trend.market_structure_engine
        phase = str(getattr(engine, "short_term_phase", ""))
        if phase in {"RECLAIM_CONFIRMED", "HEALTHY_PULLBACK", "BREAKOUT_CONFIRMED"}:
            timing, action = TimingStatus.TIMING_ENTRY_READY, FinalAction.WAIT
        elif phase:
            timing, action = TimingStatus.WATCH, FinalAction.WATCH
        else:
            timing, action = TimingStatus.WAIT, FinalAction.WAIT
        options_status, option_reasons = OptionsStatus.NOT_EVALUATED, ()
        feature_date = getattr(engine, "feature_max_date", None)
        if timing in {TimingStatus.TIMING_ENTRY_READY, TimingStatus.WATCH} and options_reader is not None:
            from .options import shortlist_spreads
            try:
                chain = options_reader(symbol, pd.Timestamp(feature_date).normalize())
                close = float(daily.iloc[-1].close)
                atr = float(getattr(trend.support, "current_atr", 0) or 0)
                candidates = shortlist_spreads(symbol, feature_date, close, atr, chain, rules=option_rules or {})
                options_status = OptionsStatus.PASS if candidates else OptionsStatus.REJECT
                option_reasons = ("OPTIONS_SHORTLIST_PASS" if candidates else "NO_QUALIFYING_SPREAD",)
            except Exception as exc:
                options_status = OptionsStatus.DATA_BLOCKED
                option_reasons = (str(exc).strip() or "OPTIONS_DATA_BLOCKED",)
        reasons = tuple(getattr(engine, "reason_codes", ())) + option_reasons or ("TIMING_EVALUATED",)
        return TickerScanResult(symbol, run_id, asof, entry.status, timing, options_status,
            final_action=action, reason_codes=reasons, feature_max_date=str(feature_date),
            latency_ms=(perf_counter()-started)*1000)
    except Exception as exc:
        failure_code = str(exc).strip()
        reasons = (failure_code,) if failure_code in {
            "DATASET_CHECKSUM_MISMATCH", "DATASET_FINGERPRINT_MISMATCH",
            "INSUFFICIENT_FEATURE_WARMUP", "DUPLICATE_CANONICAL_PRICE_KEY",
            "DATASET_PROVENANCE_INCOMPLETE", "GENERATION_NOT_VERIFIED",
        } else ("DAILY_TIMING_FAILED", type(exc).__name__)
        return TickerScanResult(symbol, run_id, asof, EligibilityStatus.DATA_BLOCKED,
            final_action=FinalAction.DATA_FAILED, reason_codes=reasons,
            latency_ms=(perf_counter()-started)*1000)


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
                 option_rules=None, event_status_reader=None,
                 portfolio_status_reader=None, static_metadata_reader=None,
                 daily_handle_resolver=None, auto_prepare_data=True,
                 refresh_policy="INCREMENTAL_IF_NEEDED", max_data_workers=4,
                 max_scan_workers=None) -> PoolScanResult:
    """Run the non-mutating U1 funnel.

    Options, events, and portfolio stages intentionally remain not evaluated
    until their adapters are integrated; no options chain is read here.
    """
    if mode not in {"PREMARKET", "INTRADAY", "EOD"}:
        raise ValueError("mode must be PREMARKET, INTRADAY, or EOD")
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    if event_policy not in {"HOLD_TO_EXPIRY", "PLANNED_EARLY_EXIT"}:
        raise ValueError("unsupported event policy")
    if event_policy == "PLANNED_EARLY_EXIT" and (planned_exit_before_event_sessions is None or planned_exit_before_event_sessions < 1):
        raise ValueError("planned early exit requires positive exit buffer sessions")
    if symbols is None:
        if universe_id in {"core_watchlist", "pcs_universe"}:
            spec = UniverseSpec.from_config("config/market_universe.yaml")
            spec = UniverseSpec(spec.universe_id, spec.symbols, spec.version, "CORE_WATCHLIST", spec.fingerprint)
        elif universe_id in {None, "global_pcs_candidates"}:
            spec = UniverseSpec.from_global_candidates()
        else:
            spec = UniverseSpec.from_file(universe_id)
    else:
        spec = UniverseSpec.from_symbols(symbols, universe_id=universe_id or "explicit")
    run_id = uuid.uuid4().hex
    asof = _as_of(as_of)
    access = data_access or PCSDataAccess()
    benchmark = None
    try:
        resolver = daily_handle_resolver or resolve_active_verified_daily_handle
        benchmark_handle = resolver(benchmark_symbol, asof, 200, data_access=access)
        benchmark = access.read_verified_dataset(benchmark_handle, end_date=asof,
                                                 required_warmup_rows=200)
    except Exception:
        benchmark = None
    completed = completed_daily_cutoff(benchmark, asof, mode) if benchmark is not None else None
    if benchmark is not None and completed is not None:
        benchmark = benchmark[pd.to_datetime(benchmark["date"]).dt.normalize() <= completed].copy()
    snapshot = PoolRunSnapshot(run_id, asof, mode, str(completed.date()) if completed is not None else None, f"{spec.universe_id}:{spec.version}:{spec.universe_role}:{len(spec.symbols)}:{spec.fingerprint}",
                               benchmark_handles={benchmark_symbol: "PINNED" if benchmark is not None else "UNAVAILABLE"})
    outcomes = run_symbol_workers(spec.symbols,
        lambda symbol: _evaluate_symbol(symbol, run_id=run_id, asof=asof, access=access,
            benchmark=benchmark, benchmark_symbol=benchmark_symbol,
            options_reader=options_reader, option_rules=option_rules,
            daily_asof=str(completed.date()) if completed is not None else None,
            static_metadata_reader=static_metadata_reader,
            daily_handle_resolver=daily_handle_resolver, auto_prepare_data=auto_prepare_data,
            refresh_policy=refresh_policy), max_workers=(max_scan_workers or max_workers))
    results = [outcome.value if outcome.value is not None else TickerScanResult(
        outcome.symbol, run_id, asof, EligibilityStatus.DATA_BLOCKED,
        final_action=FinalAction.DATA_FAILED, reason_codes=outcome.reason_codes)
        for outcome in outcomes]
    summary = {"raw_count": len(spec.symbols),
               "hard_excluded_count": sum(r.eligibility_status == EligibilityStatus.HARD_EXCLUDED for r in results),
               "data_blocked_count": sum(r.eligibility_status == EligibilityStatus.DATA_BLOCKED for r in results),
               "pcs_eligible_count": sum(r.eligibility_status == EligibilityStatus.PCS_ELIGIBLE for r in results),
               "dormant_count": sum(r.timing_status == TimingStatus.DORMANT for r in results),
               "timing_watch_count": sum(r.timing_status == TimingStatus.WATCH for r in results),
               "timing_entry_ready_count": sum(r.timing_status == TimingStatus.TIMING_ENTRY_READY for r in results),
               "options_check_count": 0,
               "pcs_trade_ready_count": 0,
               "temp_blocked_count": sum(r.final_action == FinalAction.TEMP_BLOCKED for r in results),
               "rejected_count": sum(r.final_action == FinalAction.REJECTED for r in results),
               "missing_ticker_decisions": len(spec.symbols)-len(results)}
    if options_reader is not None:
        summary["options_check_count"] = sum(row.options_status != OptionsStatus.NOT_EVALUATED for row in results)
    if event_status_reader is not None or portfolio_status_reader is not None:
        from .final_gates import finalize_ticker_result
        finalized = []
        for row in results:
            event_status = event_status_reader(row.symbol, row) if event_status_reader is not None else "EVENT_DATA_STALE"
            portfolio_status = portfolio_status_reader(row.symbol, row) if portfolio_status_reader is not None else "PORTFOLIO_DATA_STALE"
            finalized.append(finalize_ticker_result(row, event_status=event_status,
                                                    portfolio_status=portfolio_status))
        results = finalized
        summary["pcs_trade_ready_count"] = sum(row.final_action == FinalAction.PCS_TRADE_READY for row in results)
    counters = {
        "ordinary_reader_calls": 0,
        "options_reader_calls": summary["options_check_count"],
        "provider_calls": 0, "promotion_calls": 0, "recovery_calls": 0,
    }
    result = PoolScanResult(snapshot, tuple(results), summary, counters=counters)
    if output_directory is not None:
        from .artifacts import persist_pool_artifacts
        persist_pool_artifacts(result, output_directory)
    from .validation import validate_pool_result
    validate_pool_result(result, spec.symbols)
    return result


__all__ = ["run_pcs_pool"]
