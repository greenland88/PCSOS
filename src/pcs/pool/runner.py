"""U1 ticker-pool runner: raw universe, static data readiness, and daily timing."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from time import perf_counter
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, Mapping, Sequence
import uuid
import os
import traceback
from threading import RLock
from pathlib import Path
from math import isfinite

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.strategy_readiness import (resolve_active_verified_daily_handle,
                                          resolve_active_verified_options_handle)
from pcs.data.control_plane import MarketDataRequirements, ensure_market_data
from pcs.data.canonical_generations import admit_migrated_daily_symbol
from pcs.trend.snapshot import build_trend_snapshot
from pcs.trend.interpretation import interpret_trend
from pcs.trend.scoring import score_trend
from pcs.entry.trend_gate import evaluate_trend_gate
from pcs.entry.pullback_gate import evaluate_pullback_gate
from .models import (EligibilityStatus, FinalAction, OptionsStatus, PoolRunSnapshot,
                     PoolScanResult, TickerScanResult, TimingStatus)
from .registry import UniverseSpec, evaluate_static_eligibility, resolve_pool_universe
from .modes import resolve_effective_market_session
from .runtime import PoolRuntime
from .concurrency import run_symbol_workers
from .options import discover_spreads, load_pool_option_rules

_PREPARATION_LOCK = RLock()


@dataclass(frozen=True)
class DailyReadiness:
    status: str
    reason_codes: tuple[str, ...] = ()


def _validate_options_quote_session(chain: pd.DataFrame, expected_session) -> pd.DataFrame:
    """Require one verifiable quote session before Stage-B discovery."""
    expected = pd.Timestamp(expected_session).normalize()
    if chain is None or chain.empty:
        raise ValueError("OPTIONS_QUOTE_SESSION_UNVERIFIED")
    evidence = []
    for field in ("trade_date", "quote_as_of", "as_of"):
        if field not in chain.columns:
            continue
        values = pd.to_datetime(chain[field], errors="coerce")
        values = values.dropna().map(lambda value: pd.Timestamp(value).normalize())
        if not values.empty:
            evidence.extend(values.tolist())
    if not evidence:
        raise ValueError("OPTIONS_QUOTE_SESSION_UNVERIFIED")
    sessions = {value for value in evidence}
    if len(sessions) != 1 or expected not in sessions:
        raise ValueError("OPTIONS_QUOTE_SESSION_MISMATCH")
    normalized = chain.copy()
    for field in ("trade_date", "quote_as_of"):
        if field in normalized.columns:
            normalized[field] = pd.to_datetime(normalized[field], errors="coerce").dt.normalize()
    return normalized


def _candidate_record(candidate) -> dict:
    """Serialize the canonical candidate without retaining a live object."""
    if hasattr(candidate, "to_dict"):
        return dict(candidate.to_dict())
    try:
        return dict(asdict(candidate))
    except TypeError:
        return dict(vars(candidate))


def _daily_preflight(symbols, access, decision_date):
    """Build one stable, read-only daily readiness index for this run."""
    if not hasattr(access, "_resolve_route") or not hasattr(access, "_read_manifest"):
        return {str(symbol).strip().upper(): DailyReadiness("READY") for symbol in symbols}
    manifest_cache = {}
    index = {}
    decision = pd.Timestamp(decision_date).normalize()
    for symbol in symbols:
        s = str(symbol).strip().upper()
        try:
            try:
                _, manifest_path, _ = access._resolve_route("daily", s)
            except Exception:
                from pcs.data.control_plane import SourceResolver
                if SourceResolver().resolve("daily"):
                    index[s] = DailyReadiness("PREP_REQUIRED", ("MANIFEST_ROUTE_MISSING",))
                else:
                    index[s] = DailyReadiness("HARD_BLOCKED", ("BLOCKED_NO_AUTHORIZED_SOURCE",))
                continue
            key = str(Path(manifest_path).resolve())
            if key not in manifest_cache:
                manifest_cache[key] = access._read_manifest(Path(manifest_path))
            manifest = manifest_cache[key]
            required = {"dataset", "symbol", "active_generation", "min_date", "max_date"}
            if manifest.empty or not required.issubset(manifest.columns):
                index[s] = DailyReadiness("PREP_REQUIRED", ("MANIFEST_ROUTE_MISSING",)); continue
            rows = manifest[(manifest.dataset.astype(str) == "daily") &
                            manifest.symbol.astype(str).str.upper().eq(s)]
            active = rows[rows.active_generation.notna() &
                          rows.active_generation.astype(str).str.strip().ne("") &
                          rows.active_generation.astype(str).str.lower().ne("nan")]
            if active.empty:
                index[s] = DailyReadiness("PREP_REQUIRED", ("ACTIVE_GENERATION_MISSING",)); continue
            covered = active[pd.to_datetime(active.min_date, errors="coerce").le(decision) &
                            pd.to_datetime(active.max_date, errors="coerce").ge(decision)]
            if covered.empty:
                max_date = pd.to_datetime(active.max_date, errors="coerce").max()
                reason = "DAILY_STALE" if pd.notna(max_date) and max_date < decision else "INSUFFICIENT_FEATURE_WARMUP"
                index[s] = DailyReadiness("PREP_REQUIRED", (reason,)); continue
            # Latest-session coverage and historical warmup are independent.
            # A current quarter is normally much smaller than the 200-row
            # indicator warmup, so count all active, non-future partitions.
            history = active[pd.to_datetime(active.min_date, errors="coerce").le(decision) &
                             pd.to_datetime(active.max_date, errors="coerce").le(decision)].copy()
            identity_columns = [column for column in ("partition_ids", "parquet_path", "active_generation")
                                if column in history.columns]
            if identity_columns:
                history = history.drop_duplicates(identity_columns, keep="last")
            if "row_count" not in history:
                index[s] = DailyReadiness("PREP_REQUIRED", ("INSUFFICIENT_FEATURE_WARMUP",)); continue
            counts = pd.to_numeric(history.row_count, errors="coerce")
            if counts.isna().any() or counts.sum() < 200:
                index[s] = DailyReadiness("PREP_REQUIRED", ("INSUFFICIENT_FEATURE_WARMUP",)); continue
            index[s] = DailyReadiness("READY")
        except Exception as exc:
            index[s] = DailyReadiness("HARD_BLOCKED", (str(exc).strip() or "DAILY_READINESS_UNAVAILABLE",))
    return index


def _daily_requirements(symbol: str, effective_daily_session: str) -> MarketDataRequirements:
    day = pd.Timestamp(effective_daily_session).normalize()
    return MarketDataRequirements(
        symbol=symbol,
        required_start=str((day - pd.Timedelta(days=420)).date()),
        required_end=str(day.date()),
        datasets=("daily",),
        decision_as_of=str(day.date()),
        required_history_rows=200,
    )


def _prepare_daily_symbol(symbol: str, access: PCSDataAccess, effective_daily_session: str) -> dict:
    """Prepare one daily dependency through the canonical control plane."""
    symbol = str(symbol).strip().upper()
    req = _daily_requirements(symbol, effective_daily_session)
    try:
        # An independently verified active generation is authoritative.  Do
        # not let stale legacy migration files prevent its incremental refresh.
        active_state = _daily_preflight((symbol,), access, effective_daily_session)[symbol]
        if active_state.status == "READY":
            return {"symbol": symbol, "attempted": False, "result": None,
                    "result_status": "ALREADY_COMPLETE", "reason_codes": (),
                    "provider_calls": 0, "provider_coverage_count": 0, "promotion_calls": 0,
                    "admission_status": "ACTIVE_CANONICAL_READY"}
        if active_state.status == "PREP_REQUIRED" and any(code in {"DAILY_STALE", "CANONICAL_DAILY_STALE"} for code in active_state.reason_codes):
            result = ensure_market_data(symbol, req, access=access)
            outcomes = tuple(getattr(result, "import_outcomes", ()) or ())
            return {"symbol": symbol, "attempted": True, "result": result,
                    "result_status": str(getattr(result, "status", "")),
                    "reason_codes": tuple(getattr(result, "reason_codes", ()) or ()) or active_state.reason_codes,
                    "provider_calls": sum(1 for item in outcomes if str(item.get("status", "")).upper() != "REUSED"),
                    "provider_coverage_count": len(getattr(result, "provider_coverage", ()) or ()),
                    "promotion_calls": len(getattr(result, "promoted_partitions", ()) or ()),
                    "admission_status": "ACTIVE_CANONICAL_STALE"}
        admission = admit_migrated_daily_symbol(symbol, decision_as_of=effective_daily_session,
                                                required_warmup_sessions=200, data_access=access)
        admission_status = str(admission.get("status", ""))
        admission_reasons = tuple(admission.get("reason_codes", ()) or ())
        if admission_status == "MIGRATED_CANONICAL_INVALID" or admission_status in {"MIGRATION_CATALOG_AMBIGUOUS", "MIGRATION_CATALOG_NOT_SUCCESS"}:
            return {"symbol": str(symbol).upper(), "attempted": True, "result": None,
                    "result_status": admission_status, "reason_codes": admission_reasons,
                    "provider_calls": 0, "provider_coverage_count": 0, "promotion_calls": 0,
                    "admission_status": admission_status, "admission_result": admission}
        needs_incremental = admission_status == "ADMITTED_NEEDS_INCREMENTAL"
        result = ensure_market_data(symbol, req, access=access) if (
            admission_status in {"MIGRATION_CATALOG_MISSING", "MIGRATION_CATALOG_NOT_SUCCESS",
                                 "MIGRATION_PHYSICAL_MISSING", ""} or needs_incremental) else None
        status = str(getattr(result, "status", ""))
        reasons = tuple(dict.fromkeys((*admission_reasons, *(getattr(result, "reason_codes", ()) or ()))))
        return {"symbol": str(symbol).upper(), "attempted": True, "result": result,
                "result_status": status or admission_status, "reason_codes": reasons,
                "provider_calls": sum(1 for item in (getattr(result, "import_outcomes", ()) or ())
                                      if str(item.get("status", "")).upper() != "REUSED"),
                "provider_coverage_count": len(getattr(result, "provider_coverage", ()) or ()),
                "promotion_calls": len(getattr(result, "promoted_partitions", ()) or ()) + len(admission.get("promoted_partitions", ()) or ()),
                "admission_status": admission_status, "admission_result": admission}
    except Exception as exc:
        return {"symbol": str(symbol).upper(), "attempted": True, "result": None,
                "result_status": "FAILED", "reason_codes": (str(exc).strip() or type(exc).__name__,),
                "provider_calls": 0, "provider_coverage_count": 0, "promotion_calls": 0}


def _bounded_daily_preparation(symbols, access, effective_daily_session, *, max_workers, timeout_seconds):
    # Windows cannot safely replace the shared CSV manifest while several
    # preparation threads are committing generations.  Keep the canonical
    # promotion path single-writer on that platform; provider/read-only scans
    # remain bounded by the caller's worker setting.
    preparation_workers = 1 if os.name == "nt" else max_workers
    outcomes = run_symbol_workers(
        symbols, lambda symbol: _prepare_daily_symbol(symbol, access, effective_daily_session),
        max_workers=preparation_workers, timeout_seconds=timeout_seconds, include_error_details=True,
    )
    results = {}
    for outcome in outcomes:
        if outcome.value is not None:
            results[outcome.symbol] = outcome.value
            continue
        not_started = "STAGE_DEADLINE_NOT_STARTED" in outcome.reason_codes
        timed_out = not_started or "WORKER_TIMEOUT" in outcome.reason_codes
        results[outcome.symbol] = {
            "symbol": outcome.symbol, "attempted": not not_started,
            "result_status": "TIMEOUT" if timed_out else "FAILED",
            "reason_codes": (("DAILY_PREPARATION_NOT_STARTED",) if not_started else
                             ("DAILY_PREPARATION_TIMEOUT",) if timed_out else outcome.reason_codes),
            "provider_calls": 0, "provider_coverage_count": 0, "promotion_calls": 0,
        }
    counters = {"attempted": sum(bool(x.get("attempted")) for x in results.values()),
                "provider_calls": sum(x.get("provider_calls", 0) for x in results.values()),
                "provider_coverage_count": sum(x.get("provider_coverage_count", 0) for x in results.values()),
                "promotion_calls": sum(x.get("promotion_calls", 0) for x in results.values())}
    return results, counters


def _revalidate_daily(symbols, access, effective_daily_session, resolver):
    """Re-read readiness and pin-test each prepared daily dependency."""
    states = _daily_preflight(symbols, access, effective_daily_session)
    for symbol in symbols:
        state = states[symbol]
        if state.status != "READY":
            continue
        try:
            resolver(symbol, effective_daily_session, 200, data_access=access)
        except Exception as exc:
            states[symbol] = DailyReadiness(
                "PREP_REQUIRED", (str(exc).strip() or "DAILY_VERIFIED_READ_FAILED",))
    return states


def _audit_verified_daily(states, symbols, access, effective_daily_session, resolver):
    """Verify metadata-READY dependencies without fetching or writing."""
    if not hasattr(access, "_resolve_route") or not hasattr(access, "_read_manifest"):
        return states
    hard_codes = {
        "DATASET_CHECKSUM_MISMATCH", "READ_BACK_CHECKSUM_MISMATCH",
        "DATASET_PROVENANCE_INCOMPLETE", "DUPLICATE_CANONICAL_PRICE_KEY",
        "GENERATION_NOT_VERIFIED", "CANONICAL_PERMISSION_REPAIR_REQUIRES_OWNER",
    }
    for symbol in symbols:
        state = states[symbol]
        if state.status != "READY":
            continue
        try:
            resolver(symbol, effective_daily_session, 200, data_access=access)
        except Exception as exc:
            code = str(exc).strip() or "DAILY_VERIFIED_READ_FAILED"
            states[symbol] = DailyReadiness(
                "HARD_BLOCKED" if code in hard_codes else "PREP_REQUIRED", (code,))
    return states


def _evaluate_symbol(symbol, *, run_id, asof, access, benchmark, benchmark_symbol,
                     options_reader, option_rules, daily_asof=None, static_metadata_reader=None,
                     daily_handle_resolver=None, auto_prepare_data=True,
                     refresh_policy="INCREMENTAL_IF_NEEDED", runtime=None,
                     options_prepare=None, options_enabled=None, mode="EOD"):
    started = perf_counter()
    metadata = static_metadata_reader(symbol) if static_metadata_reader is not None else None
    entry = evaluate_static_eligibility(symbol, metadata)
    if entry.status != EligibilityStatus.PCS_ELIGIBLE:
        return TickerScanResult(symbol, run_id, asof, entry.status,
            final_action=FinalAction.DATA_FAILED, reason_codes=entry.reason_codes,
            latency_ms=(perf_counter()-started)*1000)
    try:
        resolver = daily_handle_resolver or resolve_active_verified_daily_handle
        day = pd.Timestamp(daily_asof or asof).normalize()

        def prepare_daily():
            prep = MarketDataRequirements(symbol=symbol, required_start=str((day - pd.Timedelta(days=420)).date()),
                                          required_end=str(day.date()), datasets=("daily",),
                                          decision_as_of=str(day.date()), required_history_rows=200)
            with _PREPARATION_LOCK:
                active_state = _daily_preflight((symbol,), access, str(day.date()))[symbol]
                if active_state.status == "READY":
                    if runtime is not None and hasattr(runtime, "refresh_manifest_snapshot"):
                        runtime.refresh_manifest_snapshot()
                    return
                if active_state.status == "PREP_REQUIRED" and any(code in {"DAILY_STALE", "CANONICAL_DAILY_STALE"} for code in active_state.reason_codes):
                    ensure_market_data(symbol, prep, access=access)
                    if runtime is not None and hasattr(runtime, "refresh_manifest_snapshot"):
                        runtime.refresh_manifest_snapshot()
                    return
                # A verified canonical object with a missing pointer can be
                # adopted locally before any provider path is considered.
                try:
                    admission = admit_migrated_daily_symbol(
                        symbol, decision_as_of=str(day.date()), required_warmup_sessions=200,
                        data_access=access)
                except (OSError, ValueError, KeyError):
                    admission = {"status": "MIGRATION_ADMISSION_FAILED"}
                if admission.get("status") in {"MIGRATED_CANONICAL_INVALID", "MIGRATION_CATALOG_AMBIGUOUS", "MIGRATION_CATALOG_NOT_SUCCESS"}:
                    raise ValueError(admission.get("reason_codes", ("MIGRATION_ADMISSION_FAILED",))[0])
                if admission.get("status") in {"MIGRATION_CATALOG_MISSING", "MIGRATION_PHYSICAL_MISSING", "ADMITTED_NEEDS_INCREMENTAL", "MIGRATION_ADMISSION_FAILED"}:
                    ensure_market_data(symbol, prep, access=access)
                if runtime is not None and hasattr(runtime, "refresh_manifest_snapshot"):
                    runtime.refresh_manifest_snapshot()

        runtime = runtime or PoolRuntime(access)
        handle = runtime.resolve_daily_handle(
            symbol, daily_asof or asof, 200, resolver=resolver,
            prepare=prepare_daily,
            auto_prepare=auto_prepare_data and daily_handle_resolver is None)
        daily = runtime.read_daily(handle, end_date=daily_asof or asof,
                                   required_warmup_rows=200)
        if benchmark is None or daily.empty:
            raise ValueError("BENCHMARK_OR_DAILY_DATA_UNAVAILABLE")
        # Each worker receives an independent immutable snapshot boundary;
        # trend helpers may construct intermediate columns internally.
        timing_reasons = []
        timing_warnings = []
        trend_gate = pullback_gate = interpretation = trend_score = trend = None
        try:
            trend = build_trend_snapshot(daily.copy(deep=True), benchmark.copy(deep=True),
                                         as_of_date=str(day.date()), symbol=symbol, benchmark=benchmark_symbol)
            interpretation = interpret_trend(trend)
            trend_score = score_trend(trend, interpretation)
            trend_gate = evaluate_trend_gate(trend_score, interpretation, trend)
            pullback_gate = evaluate_pullback_gate(trend_gate, trend, interpretation)
            timing_warnings = list(getattr(trend, "warnings", ()) or ())
            for result in (interpretation, trend_score, trend_gate, pullback_gate):
                timing_warnings.extend(getattr(result, "warnings", ()) or ())
            timing_warnings = list(dict.fromkeys(timing_warnings))
            timing_reasons.extend(getattr(interpretation, "reasons", ()) or ())
            timing_reasons.extend(getattr(trend_score, "reasons", ()) or ())
            timing_reasons.extend(getattr(trend_gate, "reasons", ()) or ())
            timing_reasons.extend(getattr(pullback_gate, "reasons", ()) or ())
            if not all(getattr(result, "available", False) for result in
                       (trend, interpretation, trend_score, trend_gate, pullback_gate)):
                timing, action = TimingStatus.WAIT, FinalAction.WAIT
                timing_reasons.insert(0, "TIMING_EVIDENCE_UNAVAILABLE")
            elif trend_gate.trend_gate_result == "REJECT" or pullback_gate.pullback_gate_result == "REJECT":
                timing, action = TimingStatus.WAIT, FinalAction.REJECTED
                timing_reasons.insert(0, "UNDERLYING_STRUCTURAL_REJECT")
            elif trend_gate.trend_gate_result == "WATCH":
                timing, action = TimingStatus.WATCH, FinalAction.WATCH
            elif (trend_gate.trend_gate_result == "PASS" and
                  pullback_gate.pullback_gate_result == "WAIT"):
                timing, action = TimingStatus.WAIT, FinalAction.WAIT
            elif (trend_gate.trend_gate_result == "PASS" and
                  pullback_gate.pullback_gate_result == "PASS"):
                timing, action = TimingStatus.TIMING_ENTRY_READY, FinalAction.WAIT
            else:
                timing, action = TimingStatus.WAIT, FinalAction.WAIT
        except Exception:
            timing, action = TimingStatus.WAIT, FinalAction.WAIT
            timing_reasons = ["TIMING_EVIDENCE_UNAVAILABLE"]
        timing_reasons.extend(timing_warnings)
        timing_reasons = tuple(dict.fromkeys(timing_reasons))
        options_status, option_reasons = OptionsStatus.NOT_EVALUATED, ()
        candidates = ()
        discovered_contracts = ()
        engine = getattr(trend, "market_structure_engine", None)
        feature_date = getattr(engine, "feature_max_date", None)
        if options_enabled is None:
            options_enabled = options_reader is not None
        if (timing == TimingStatus.TIMING_ENTRY_READY and
                mode in {"PREMARKET", "INTRADAY"} and options_reader is None):
            option_reasons = ("LIVE_OPTIONS_SOURCE_REQUIRED",)
        elif timing == TimingStatus.TIMING_ENTRY_READY and options_enabled:
            try:
                if options_prepare is None and options_reader is None:
                    option_day = pd.Timestamp(feature_date).normalize()

                    def options_prepare():
                        req = MarketDataRequirements(
                            symbol=symbol, required_start=str(option_day.date()),
                            required_end=str(option_day.date()), datasets=("options",),
                            decision_as_of=str(option_day.date()), option_type="PUT",
                            min_dte=30, max_dte=45, required_history_rows=0)
                        ensure_market_data(symbol, req, access=access)

                option_day = (pd.Timestamp(asof).normalize() if options_reader is not None and
                              mode in {"PREMARKET", "INTRADAY"} else pd.Timestamp(feature_date).normalize())
                if options_reader is not None:
                    chain = runtime.read_options(
                        symbol=symbol, trade_date=option_day, reader=options_reader)
                else:
                    try:
                        option_handle = runtime.resolve_options(symbol, str(option_day.date()))
                    except Exception:
                        if not auto_prepare_data or options_prepare is None:
                            raise
                        options_prepare()
                        option_handle = resolve_active_verified_options_handle(
                            symbol, str(option_day.date()), data_access=access)
                    chain = runtime.read_options_handle(option_handle, start_date=str(option_day.date()), end_date=str(option_day.date()))
                chain = _validate_options_quote_session(chain, option_day)
                close = float(daily.iloc[-1].close)
                atr = float(getattr(trend.support, "current_atr", 0) or 0)
                contract_entry_date = option_day
                candidates = discover_spreads(symbol, contract_entry_date, close, atr, chain,
                                              rules=option_rules)
                options_status = OptionsStatus.DISCOVERED if candidates else OptionsStatus.REJECT
                option_reasons = ("CONTRACT_CANDIDATES_DISCOVERED" if candidates
                                  else "NO_STRUCTURALLY_VALID_PCS",)
                discovered_contracts = tuple(_candidate_record(candidate) for candidate in candidates)
            except Exception as exc:
                options_status = OptionsStatus.DATA_BLOCKED
                option_reasons = (str(exc).strip() or "OPTIONS_DATA_BLOCKED",)
        reasons = timing_reasons + option_reasons or ("TIMING_EVALUATED",)
        return TickerScanResult(symbol, run_id, asof, entry.status, timing, options_status,
            final_action=action, reason_codes=reasons, feature_max_date=str(feature_date),
            latency_ms=(perf_counter()-started)*1000,
            spread_count=len(candidates),
            discovered_contracts=discovered_contracts,
            structural_trend=getattr(engine, "structural_trend", None),
            short_term_phase=getattr(engine, "short_term_phase", None),
            trend_gate_reasons=tuple(getattr(trend_gate, "reasons", ()) or ()),
            pullback_gate_reasons=tuple(getattr(pullback_gate, "reasons", ()) or ()),
            warnings=tuple(timing_warnings))
    except Exception as exc:
        trace_dir = os.getenv("PCS_POOL_SCAN_TRACEBACK_DIR")
        if trace_dir:
            target = Path(trace_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{str(symbol).upper()}.traceback.txt").write_text(
                f"symbol={str(symbol).upper()}\nstage=timing_or_options\nexception={type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
                encoding="utf-8")
        failure_code = str(exc).strip()
        reasons = (failure_code,) if failure_code in {
            "DATASET_CHECKSUM_MISMATCH", "DATASET_FINGERPRINT_MISMATCH",
            "INSUFFICIENT_FEATURE_WARMUP", "DUPLICATE_CANONICAL_PRICE_KEY",
            "DATASET_PROVENANCE_INCOMPLETE", "GENERATION_NOT_VERIFIED",
            "ACTIVE_GENERATION_MISSING", "MANIFEST_ROUTE_MISSING",
            "DAILY_STALE", "BLOCKED_NO_AUTHORIZED_SOURCE",
            "CANONICAL_DAILY_STALE", "SOURCE_UNAVAILABLE",
        } else ("DAILY_TIMING_FAILED", type(exc).__name__)
        return TickerScanResult(symbol, run_id, asof, EligibilityStatus.DATA_BLOCKED,
            final_action=FinalAction.DATA_FAILED, reason_codes=reasons,
            latency_ms=(perf_counter()-started)*1000)


def _as_of(value) -> str:
    if value == "latest":
        # Market-session semantics are keyed to the exchange's local date;
        # UTC after midnight must not advance an NYSE PREMARKET run early.
        return datetime.now(ZoneInfo("America/New_York")).isoformat()
    return pd.Timestamp(value).isoformat()


def reconcile_pool_scan_results(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Build an explicit ticker-level delta for comparable pool scans."""
    def rows(value):
        return {str(row.get("symbol", "")).upper(): row for row in value.get("ticker_results", ())}
    left, right = rows(before), rows(after)
    fields = ("effective_daily_session", "universe_snapshot_id", "mode", "manifest_snapshot_id")
    identity = {f: (before.get("snapshot", {}).get(f), after.get("snapshot", {}).get(f)) for f in fields}
    comparable = all(a == b for a, b in identity.values() if a is not None and b is not None)
    keys = ("eligibility_status", "initial_daily_readiness", "timing_status", "options_status", "final_action")
    changed = []
    for symbol in sorted(set(left) & set(right)):
        a, b = left[symbol], right[symbol]
        if any(a.get(k) != b.get(k) or a.get("reason_codes", ()) != b.get("reason_codes", ()) for k in keys):
            changed.append({"symbol": symbol, "before": {k: a.get(k) for k in (*keys, "reason_codes")},
                            "after": {k: b.get(k) for k in (*keys, "reason_codes")}})
    ready_left = {s for s, row in left.items() if row.get("eligibility_status") == "PCS_ELIGIBLE"}
    ready_right = {s for s, row in right.items() if row.get("eligibility_status") == "PCS_ELIGIBLE"}
    return {"comparable": comparable, "identity": identity, "added": sorted(set(right)-set(left)),
            "removed": sorted(set(left)-set(right)), "ready_added": sorted(ready_right-ready_left),
            "ready_removed": sorted(ready_left-ready_right), "changed": changed,
            "before_count": len(left), "after_count": len(right)}


def run_pcs_pool(*, universe_id: str | None = None, symbols: Sequence[str] | None = None,
                 as_of: datetime | str = "latest",
                 mode: Literal["PREMARKET", "INTRADAY", "EOD"],
                 data_mode: Literal["PREPARE_THEN_SCAN", "READ_ONLY"] = "READ_ONLY",
                 strategies: Sequence[str] | None = None,
                 event_policy: Literal["HOLD_TO_EXPIRY", "PLANNED_EARLY_EXIT"] = "HOLD_TO_EXPIRY",
                 planned_exit_before_event_sessions: int | None = None,
                 max_workers: int = 8, output_directory=None,
                 data_access: PCSDataAccess | None = None,
                 benchmark_symbol: str = "QQQ", options_reader=None,
                 option_rules=None, event_status_reader=None,
                 portfolio_status_reader=None, static_metadata_reader=None,
                 daily_handle_resolver=None, auto_prepare_data=False,
                 refresh_policy="INCREMENTAL_IF_NEEDED", max_data_workers=4,
                 max_scan_workers=None, stage_timeout_seconds: float | None = 60.0,
                 timeout_seconds: float | None = None) -> PoolScanResult:
    """Scan pinned daily/options inputs; preparation requires explicit opt-in."""
    if mode not in {"PREMARKET", "INTRADAY", "EOD"}:
        raise ValueError("mode must be PREMARKET, INTRADAY, or EOD")
    if data_mode not in {"PREPARE_THEN_SCAN", "READ_ONLY"}:
        raise ValueError("data_mode must be PREPARE_THEN_SCAN or READ_ONLY")
    if data_mode == "PREPARE_THEN_SCAN" and not auto_prepare_data:
        raise ValueError("AUTO_PREPARE_DATA_REQUIRED")
    if data_mode == "READ_ONLY" and auto_prepare_data:
        raise ValueError("AUTO_PREPARE_DATA_REQUIRES_PREPARE_THEN_SCAN")
    from pcs.data.massive_client import load_project_environment
    load_project_environment()
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    if max_data_workers < 1:
        raise ValueError("max_data_workers must be positive")
    if stage_timeout_seconds is not None and timeout_seconds is not None:
        raise ValueError("specify only one timeout")
    stage_timeout_seconds = timeout_seconds if timeout_seconds is not None else stage_timeout_seconds
    if stage_timeout_seconds is None:
        stage_timeout_seconds = 60.0
    if not isfinite(stage_timeout_seconds) or stage_timeout_seconds <= 0:
        raise ValueError("stage_timeout_seconds must be finite and positive")
    if event_policy not in {"HOLD_TO_EXPIRY", "PLANNED_EARLY_EXIT"}:
        raise ValueError("unsupported event policy")
    if event_policy == "PLANNED_EARLY_EXIT" and (planned_exit_before_event_sessions is None or planned_exit_before_event_sessions < 1):
        raise ValueError("planned early exit requires positive exit buffer sessions")
    spec = resolve_pool_universe(symbols, universe_id)
    run_id = uuid.uuid4().hex
    asof = _as_of(as_of)
    effective = resolve_effective_market_session(asof, mode, "XNYS")
    effective_asof = str(effective.date())
    access = data_access or PCSDataAccess()
    started = perf_counter()
    daily_resolver = daily_handle_resolver or resolve_active_verified_daily_handle
    if option_rules is None:
        option_rules = load_pool_option_rules()
    stage_latency: dict[str, float] = {}
    audit_started = perf_counter()
    dependencies = tuple(dict.fromkeys((*[str(s).strip().upper() for s in spec.symbols],
                                        str(benchmark_symbol).strip().upper())))
    initial = _audit_verified_daily(
        _daily_preflight(dependencies, access, effective_asof),
        dependencies, access, effective_asof, daily_resolver)
    stage_latency["readiness_audit"] = (perf_counter() - audit_started) * 1000
    prep_required = tuple(symbol for symbol in dependencies
                          if initial[symbol].status == "PREP_REQUIRED")
    hard_blocked = {symbol: initial[symbol] for symbol in dependencies
                    if initial[symbol].status == "HARD_BLOCKED"}
    prep_results = {}
    prep_counters = {"attempted": 0, "provider_calls": 0, "promotion_calls": 0}
    if data_mode == "PREPARE_THEN_SCAN" and prep_required:
        prep_started = perf_counter()
        prep_results, prep_counters = _bounded_daily_preparation(
            prep_required, access, effective_asof,
            max_workers=max_data_workers, timeout_seconds=stage_timeout_seconds)
        stage_latency["daily_preparation"] = (perf_counter() - prep_started) * 1000
    else:
        stage_latency["daily_preparation"] = 0.0
    # Never revalidate a timed-out writer while it may still be completing a transaction.
    revalidation_symbols = tuple(symbol for symbol in prep_required
                                 if prep_results.get(symbol, {}).get("result_status") != "TIMEOUT") \
        if data_mode == "PREPARE_THEN_SCAN" else ()
    revalidated = (_revalidate_daily(revalidation_symbols, access, effective_asof, daily_resolver)
                   if revalidation_symbols else {})
    prepared_ready = {symbol for symbol in revalidation_symbols
                      if revalidated.get(symbol, DailyReadiness("HARD_BLOCKED")).status == "READY"}
    stage_latency["readiness_revalidation"] = 0.0
    prep_evidence = {}
    for symbol in dependencies:
        initial_state = initial[symbol]
        result = prep_results.get(symbol)
        if initial_state.status == "READY":
            prep_evidence[symbol] = {"status": "NOT_NEEDED", "reasons": (), "attempted": False,
                                     "result_status": "ALREADY_COMPLETE", "dataset": "daily"}
        elif initial_state.status == "HARD_BLOCKED":
            prep_evidence[symbol] = {"status": "HARD_BLOCKED",
                                     "reasons": initial_state.reason_codes, "attempted": False,
                                     "result_status": "NOT_RUN", "dataset": "daily"}
        elif data_mode == "READ_ONLY":
            prep_evidence[symbol] = {"status": "READ_ONLY_NOT_PREPARED",
                                     "reasons": initial_state.reason_codes, "attempted": False,
                                     "result_status": "NOT_RUN", "dataset": "daily"}
        elif symbol in prepared_ready:
            prep_evidence[symbol] = {"status": "PREPARED_READY",
                                     "reasons": tuple(result.get("reason_codes", ()) if result else ()),
                                     "attempted": True,
                                     "result_status": result.get("result_status", "READY") if result else "READY",
                                     "dataset": "daily"}
        else:
            reasons = tuple(dict.fromkeys((*((result or {}).get("reason_codes", ())),
                                           *(revalidated.get(symbol, initial_state).reason_codes))))
            prep_evidence[symbol] = {"status": "HARD_BLOCKED" if symbol in hard_blocked else "PREPARATION_FAILED",
                                     "reasons": reasons or ("DAILY_PREPARATION_FAILED",),
                                     "attempted": True,
                                     "result_status": (result or {}).get("result_status", "FAILED"),
                                     "dataset": "daily"}

    scan_ready = {symbol for symbol in dependencies
                  if (initial[symbol].status == "READY" or symbol in prepared_ready)}
    benchmark_blocker = None
    if benchmark_symbol.upper() in hard_blocked:
        benchmark_blocker = "BENCHMARK_HARD_BLOCKED"
    elif benchmark_symbol.upper() not in scan_ready:
        benchmark_blocker = "BENCHMARK_PREPARATION_FAILED" if data_mode == "PREPARE_THEN_SCAN" else "BENCHMARK_PREP_REQUIRED"

    options_resolver = (resolve_active_verified_options_handle
                        if mode == "EOD" and options_reader is None else None)
    runtime = PoolRuntime(access=access, stage_timeout_seconds=stage_timeout_seconds,
                          daily_handle_resolver=daily_resolver,
                          options_handle_resolver=options_resolver)
    benchmark = None
    benchmark_started = perf_counter()
    if benchmark_blocker is None:
        try:
            benchmark_handle = runtime.resolve_daily(benchmark_symbol, effective_asof, 200,
                                                     resolver=daily_resolver)
            benchmark = runtime.read_daily(benchmark_handle, end_date=effective_asof,
                                           required_warmup_rows=200)
        except Exception:
            benchmark_blocker = "BENCHMARK_VERIFIED_READ_FAILED"
    stage_latency["benchmark"] = (perf_counter() - benchmark_started) * 1000
    completed = effective if benchmark is not None else None
    if benchmark is not None and completed is not None:
        benchmark = benchmark[pd.to_datetime(benchmark["date"]).dt.normalize() <= completed].copy()
    snapshot = PoolRunSnapshot(
        run_id, asof, mode,
        str(completed.date()) if completed is not None else None,
        f"{spec.universe_id}:{spec.version}:{spec.universe_role}:{len(spec.symbols)}:{spec.fingerprint}",
        benchmark_handles={benchmark_symbol: "PINNED" if benchmark is not None else "UNAVAILABLE"},
        manifest_snapshot_id=runtime.manifest_snapshot_id,
        benchmark_status="READY" if benchmark is not None else (benchmark_blocker or "BLOCKED"))
    snapshot = PoolRunSnapshot(**{**snapshot.__dict__, "requested_as_of": asof,
                                  "effective_daily_session": effective_asof})
    preflight_results = {}
    queued_symbols = []
    for symbol in spec.symbols:
        normalized = str(symbol).strip().upper()
        evidence = prep_evidence[normalized]
        if benchmark_blocker is not None:
            preflight_results[normalized] = TickerScanResult(
                normalized, run_id, asof, EligibilityStatus.DATA_BLOCKED,
                final_action=FinalAction.DATA_FAILED,
                reason_codes=(benchmark_blocker,),
                preparation_status=evidence["status"],
                preparation_reason_codes=tuple(evidence["reasons"]),
                preparation_attempted=evidence["attempted"],
                preparation_result_status=evidence["result_status"],
                prepared_dataset=evidence["dataset"],
                effective_daily_session=effective_asof,
                initial_daily_readiness=initial[normalized].status)
        elif normalized not in scan_ready:
            preflight_results[str(symbol).upper()] = TickerScanResult(
                normalized, run_id, asof, EligibilityStatus.DATA_BLOCKED,
                final_action=FinalAction.TEMP_BLOCKED if evidence["status"] != "HARD_BLOCKED" else FinalAction.DATA_FAILED,
                reason_codes=tuple(evidence["reasons"]),
                preparation_status=evidence["status"],
                preparation_reason_codes=tuple(evidence["reasons"]),
                preparation_attempted=evidence["attempted"],
                preparation_result_status=evidence["result_status"],
                prepared_dataset=evidence["dataset"],
                effective_daily_session=effective_asof,
                initial_daily_readiness=initial[normalized].status)
        else:
            queued_symbols.append(symbol)
    scan = runtime.run_stage(tuple(queued_symbols),
        lambda symbol: _evaluate_symbol(symbol, run_id=run_id, asof=asof, access=access,
            runtime=runtime,
            benchmark=benchmark, benchmark_symbol=benchmark_symbol,
            options_reader=options_reader, option_rules=option_rules,
            daily_asof=effective_asof,
            static_metadata_reader=static_metadata_reader,
            daily_handle_resolver=daily_handle_resolver, auto_prepare_data=False,
            refresh_policy=refresh_policy, options_prepare=None,
            options_enabled=(options_reader is not None or options_resolver is not None), mode=mode),
        stage_name="scan", max_workers=(max_scan_workers or max_workers), timeout_seconds=stage_timeout_seconds)
    stage_latency["scan"] = runtime.stage_latency_ms.get("scan", 0.0)
    outcomes = scan.outcomes
    worker_results = {outcome.symbol: (outcome.value if outcome.value is not None else TickerScanResult(
        outcome.symbol, run_id, asof, EligibilityStatus.DATA_BLOCKED,
        final_action=FinalAction.DATA_FAILED, reason_codes=outcome.reason_codes))
        for outcome in outcomes}
    worker_results = {
        symbol: replace(row,
                        preparation_status=prep_evidence[symbol]["status"],
                        preparation_reason_codes=tuple(prep_evidence[symbol]["reasons"]),
                        preparation_attempted=prep_evidence[symbol]["attempted"],
                        preparation_result_status=prep_evidence[symbol]["result_status"],
                        prepared_dataset=prep_evidence[symbol]["dataset"],
                        effective_daily_session=effective_asof,
                        initial_daily_readiness=initial[symbol].status)
        for symbol, row in worker_results.items()
    }
    results = []
    for symbol in spec.symbols:
        normalized = str(symbol).upper()
        results.append(preflight_results[normalized] if normalized in preflight_results
                       else worker_results[normalized])
    summary = {"raw_count": len(spec.symbols),
               "hard_excluded_count": sum(r.eligibility_status == EligibilityStatus.HARD_EXCLUDED for r in results),
               "data_blocked_count": sum(r.eligibility_status == EligibilityStatus.DATA_BLOCKED for r in results),
               "pcs_eligible_count": sum(r.eligibility_status == EligibilityStatus.PCS_ELIGIBLE for r in results),
               "dormant_count": sum(r.timing_status == TimingStatus.DORMANT for r in results),
               "timing_watch_count": sum(r.timing_status == TimingStatus.WATCH for r in results),
               "timing_entry_ready_count": sum(r.timing_status == TimingStatus.TIMING_ENTRY_READY for r in results),
               "options_check_count": 0,
               "spread_count": sum(r.spread_count for r in results),
               "pcs_trade_ready_count": 0,
               "temp_blocked_count": sum(r.final_action == FinalAction.TEMP_BLOCKED for r in results),
               "rejected_count": sum(r.final_action == FinalAction.REJECTED for r in results),
               "missing_ticker_decisions": len(spec.symbols)-len(results),
               "daily_ready_initial_count": sum(initial[s].status == "READY" for s in dependencies),
               "daily_prep_required_count": sum(initial[s].status == "PREP_REQUIRED" for s in dependencies),
               "daily_hard_blocked_initial_count": sum(initial[s].status == "HARD_BLOCKED" for s in dependencies),
               "daily_prepare_attempted_count": prep_counters["attempted"],
               "daily_provider_coverage_count": prep_counters.get("provider_coverage_count", 0),
               "daily_prepared_ready_count": sum(prep_evidence[s]["status"] == "PREPARED_READY" for s in dependencies),
               "daily_prepare_failed_count": sum(prep_evidence[s]["status"] == "PREPARATION_FAILED" for s in dependencies),
               "daily_scan_ready_count": sum(s in scan_ready for s in spec.symbols),
               "benchmark_daily_prepare_attempted": int(prep_evidence[benchmark_symbol.upper()]["attempted"]),
               "benchmark_blocked": int(benchmark_blocker is not None)}
    if options_reader is not None or options_resolver is not None:
        summary["options_check_count"] = sum(row.options_status != OptionsStatus.NOT_EVALUATED for row in results)
    if event_status_reader is not None or portfolio_status_reader is not None:
        from .final_gates import finalize_ticker_result
        by_symbol = {row.symbol: row for row in results}

        def finalize(symbol):
            row = by_symbol[symbol]
            event_status = event_status_reader(row.symbol, row) if event_status_reader is not None else "EVENT_DATA_STALE"
            portfolio_status = portfolio_status_reader(row.symbol, row) if portfolio_status_reader is not None else "PORTFOLIO_DATA_STALE"
            return finalize_ticker_result(row, event_status=event_status,
                                          portfolio_status=portfolio_status)

        final_stage = runtime.run_stage(
            tuple(by_symbol), finalize, stage_name="finalize",
            max_workers=(max_scan_workers or max_workers), timeout_seconds=stage_timeout_seconds)
        stage_latency["finalize"] = runtime.stage_latency_ms.get("finalize", 0.0)
        results = [outcome.value if outcome.value is not None else by_symbol[outcome.symbol]
                   for outcome in final_stage.outcomes]
        summary["pcs_trade_ready_count"] = sum(row.final_action == FinalAction.PCS_TRADE_READY for row in results)
    else:
        stage_latency["finalize"] = 0.0
    counters = {
        "ordinary_reader_calls": 0,
        "options_reader_calls": summary["options_check_count"],
        "provider_calls": prep_counters["provider_calls"],
        "promotion_calls": prep_counters["promotion_calls"],
        "recovery_calls": prep_counters["attempted"],
        "handle_resolution_calls": runtime.counters.get("handle_resolution_calls", 0),
        "daily_frame_reads": runtime.counters.get("daily_frame_reads", 0),
        "options_frame_reads": runtime.counters.get("options_frame_reads", 0),
    }
    stage_latency["validation"] = 0.0
    from .validation import validate_pool_result
    validation_started = perf_counter()
    summary["run_status"] = ("PARTIAL_TIMEOUT" if any("WORKER_TIMEOUT" in r.reason_codes or "STAGE_DEADLINE_NOT_STARTED" in r.reason_codes for r in results)
                              else "COMPLETED_NO_EVALUABLE_TICKERS" if not any(r.timing_status != TimingStatus.NOT_EVALUATED for r in results)
                              else "COMPLETED")
    result = PoolScanResult(snapshot, tuple(results), summary,
                            stage_latency_ms=stage_latency, counters=counters,
                            discovered_contracts=tuple(
                                candidate for row in results
                                for candidate in row.discovered_contracts))
    validate_pool_result(result, spec.symbols)
    stage_latency["validation"] = (perf_counter() - validation_started) * 1000
    stage_latency["total"] = (perf_counter() - started) * 1000
    result = PoolScanResult(snapshot, tuple(results), summary,
                            stage_latency_ms=stage_latency, counters=counters,
                            discovered_contracts=tuple(
                                candidate for row in results
                                for candidate in row.discovered_contracts))
    if output_directory is not None:
        from .artifacts import persist_pool_artifacts
        persist_pool_artifacts(result, output_directory)
    return result


__all__ = ["run_pcs_pool", "reconcile_pool_scan_results"]
