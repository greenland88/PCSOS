"""Process lifetime boundary for read-only CLI scans of the canonical runner.

Never use this supervisor for import/promotion work: terminating a writer could
interrupt its transaction. Python API stage timeouts only bound result collection.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from math import isfinite
import multiprocessing
from time import perf_counter
from typing import Any
import uuid
from pathlib import Path

from .models import (EligibilityStatus, FinalAction, OptionsStatus, PoolRunSnapshot,
                     PoolScanResult, TickerScanResult, TimingStatus)
from .registry import resolve_pool_universe


@dataclass(frozen=True)
class ReadOnlyScanRequest:
    symbols: tuple[str, ...] | None = None
    universe_id: str | None = None
    as_of: str = "latest"
    mode: str = "EOD"
    max_workers: int = 8
    stage_timeout_seconds: float = 60.0
    manifest_path: str = "data/manifests/storage_manifest.csv"
    parquet_root: str = "data/parquet"
    rules: str = "config/pcs_rules.yaml"
    output_directory: str | None = "pool_scan_runs"
    decision_context_json: str | None = None
    resume: bool = True
    new_run: bool = False
    resume_run_id: str | None = None


def _scan_worker(request: ReadOnlyScanRequest, sender: Any) -> None:
    try:
        from pcs.data.access import PCSDataAccess
        from .options import load_pool_option_rules
        from .runner import run_pcs_pool

        from .adapters import load_pool_context_adapters
        adapters = load_pool_context_adapters(request.decision_context_json, rules_path=request.rules)
        result = run_pcs_pool(
            symbols=request.symbols, universe_id=request.universe_id,
            as_of=request.as_of, mode=request.mode, data_mode="READ_ONLY",
            auto_prepare_data=False, max_workers=request.max_workers,
            stage_timeout_seconds=request.stage_timeout_seconds,
            data_access=PCSDataAccess(manifest_path=request.manifest_path,
                                      parquet_root=request.parquet_root),
            option_rules=load_pool_option_rules(request.rules),
            output_directory=request.output_directory,
            **adapters,
            resume=request.resume,
            new_run=request.new_run, resume_run_id=request.resume_run_id,
            checkpoint_callback=lambda path, identity: sender.send(("checkpoint", (path, identity))),
        )
        sender.send(("result", result))
    except Exception as exc:
        sender.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        sender.close()


def _restore_progress_row(values: dict[str, Any]) -> TickerScanResult:
    allowed = {field.name for field in fields(TickerScanResult)}
    restored = {key: value for key, value in values.items() if key in allowed}
    for key, enum in (("eligibility_status", EligibilityStatus),
                      ("timing_status", TimingStatus),
                      ("options_status", OptionsStatus),
                      ("final_action", FinalAction)):
        if key in restored:
            restored[key] = enum(restored[key])
    for key in ("reason_codes", "reentry_conditions", "trend_gate_reasons",
                "pullback_gate_reasons", "warnings", "preparation_reason_codes",
                "discovered_contracts", "selection_reason_codes"):
        if key in restored and restored[key] is not None:
            restored[key] = tuple(restored[key])
    return TickerScanResult(**restored)


def _timeout_result(request, spec, started, reason, detail):
    """Build and persist a partial result from child progress, if available."""
    from .artifacts import ProgressCheckpoint, persist_pool_artifacts
    progress = ProgressCheckpoint.read(Path(request.output_directory) / "active_progress.json") \
        if request.output_directory else None
    progress_rows = progress.get("rows", {}) if progress and progress.get("run_id") else {}
    run_id = progress.get("run_id") if progress else uuid.uuid4().hex
    metadata = progress.get("metadata", {}) if progress else {}
    snapshot_values = progress.get("snapshot") if progress else None
    if snapshot_values:
        snapshot = PoolRunSnapshot(**snapshot_values)
    else:
        snapshot = PoolRunSnapshot(
            run_id, request.as_of, request.mode, metadata.get("effective_daily_session"),
            f"{spec.universe_id}:{spec.version}:{spec.fingerprint}",
            requested_as_of=request.as_of,
            effective_daily_session=metadata.get("effective_daily_session"),
            benchmark_status="UNKNOWN_TIMEOUT")
    rows = []
    for symbol in spec.symbols:
        saved = progress_rows.get(str(symbol).strip().upper())
        if saved:
            rows.append(_restore_progress_row(saved))
        else:
            rows.append(TickerScanResult(
                str(symbol).strip().upper(), run_id, request.as_of,
                EligibilityStatus.DATA_BLOCKED, final_action=FinalAction.DATA_FAILED,
                reason_codes=(reason,), warnings=(detail,)))
    timing_evaluated = sum(row.timing_status != TimingStatus.NOT_EVALUATED for row in rows)
    summary = {
        "raw_count": len(rows), "data_blocked_count": sum(row.eligibility_status == EligibilityStatus.DATA_BLOCKED for row in rows),
        "pcs_eligible_count": sum(row.eligibility_status == EligibilityStatus.PCS_ELIGIBLE for row in rows),
        "timing_evaluated_count": timing_evaluated,
        "timing_not_evaluated_count": len(rows) - timing_evaluated,
        "timing_watch_count": sum(row.timing_status == TimingStatus.WATCH for row in rows),
        "timing_entry_ready_count": sum(row.timing_status == TimingStatus.TIMING_ENTRY_READY for row in rows),
        "options_evaluated_count": sum(row.options_status != OptionsStatus.NOT_EVALUATED for row in rows),
        "pcs_trade_ready_count": sum(row.final_action == FinalAction.PCS_TRADE_READY for row in rows),
        "missing_ticker_decisions": 0, "spread_count": sum(row.spread_count for row in rows),
        "run_status": "PARTIAL_TIMEOUT", "timeout_stage": progress.get("stage", "UNKNOWN") if progress else "UNKNOWN",
    }
    result = PoolScanResult(snapshot, tuple(rows), summary=summary,
                            stage_latency_ms={"global_timeout": (perf_counter() - started) * 1000},
                            counters={"progress_rows": len(progress_rows), "ordinary_reader_calls": 0,
                                      "options_reader_calls": 0, "provider_calls": 0,
                                      "promotion_calls": 0, "recovery_calls": 0})
    if request.output_directory:
        persist_pool_artifacts(result, request.output_directory)
    return result


def run_read_only_scan(request: ReadOnlyScanRequest, *, timeout_seconds: float = 300.0,
                       _worker: Any = _scan_worker) -> PoolScanResult:
    """Run the canonical scanner in a disposable process, returning ordered failures.

    ``timeout_seconds`` includes child startup and scan execution; cleanup adds
    at most two seconds. A completed result is retained even if a timed-out
    thread keeps the child alive. Provider preparation is forbidden; optional
    output writes are audit artifacts only and never canonical storage.
    """
    if not isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("scan timeout must be finite and positive")
    spec = resolve_pool_universe(request.symbols, request.universe_id)
    started = perf_counter()
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(request, sender), name="pcs-read-only-scan")
    reason = "POOL_SCAN_PROCESS_FAILED"
    detail = "scan process ended without a result"
    launched = False
    checkpoint_anchor = None
    try:
        process.start()
        launched = True
        sender.close()
        while True:
            remaining = max(0.0, timeout_seconds - (perf_counter() - started))
            if not receiver.poll(remaining):
                reason = "POOL_SCAN_TIMEOUT"
                detail = "read-only scan exceeded its process deadline"
                break
            try:
                kind, payload = receiver.recv()
            except EOFError:
                break
            else:
                if kind == "checkpoint":
                    checkpoint_anchor = payload
                    continue
                if kind == "result" and isinstance(payload, PoolScanResult):
                    from .validation import validate_pool_result
                    validate_pool_result(payload, spec.symbols)
                    return payload
                detail = str(payload)
                break
    finally:
        sender.close()
        receiver.close()
        if launched:
            # A returned stage result can leave uninterruptible read threads.
            # Termination is safe only because this child has no write authority.
            process.join(timeout=0.05)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.9)
            process.close()

    if checkpoint_anchor:
        import json
        from pathlib import Path
        from dataclasses import replace
        from .runner import _load_scan_checkpoint
        path, identity = checkpoint_anchor
        saved_id, saved = _load_scan_checkpoint(Path(path), identity)
        if saved_id:
            state = json.loads(Path(path).read_text(encoding="utf-8"))
            snapshot = PoolRunSnapshot(**state["snapshot"])
            # The startup anchor preserves the old cache for recovery, but
            # these rows have not yet passed this invocation's input checks.
            if state.get("stage") == "READINESS_AUDIT":
                saved = {}
            rows = tuple(
                replace(saved[symbol], run_id=snapshot.run_id, as_of=snapshot.as_of)
                if symbol in saved and saved[symbol].checkpoint_stage == "COMPLETE"
                else replace(saved[symbol], run_id=snapshot.run_id, as_of=snapshot.as_of,
                             reason_codes=("WORKER_TIMEOUT",)) if symbol in saved
                else TickerScanResult(symbol, saved_id, snapshot.as_of, EligibilityStatus.DATA_BLOCKED,
                                     reason_codes=("STAGE_DEADLINE_NOT_STARTED",))
                for symbol in spec.symbols)
            from .models import TimingStatus, OptionsStatus
            result = PoolScanResult(snapshot, rows, summary={
                "raw_count": len(rows), "run_status": "PARTIAL_TIMEOUT" if reason == "POOL_SCAN_TIMEOUT" else "FAILED",
                "timeout_count": sum("WORKER_TIMEOUT" in r.reason_codes for r in rows),
                "unprocessed_count": sum("STAGE_DEADLINE_NOT_STARTED" in r.reason_codes for r in rows),
                "pcs_eligible_count": sum(r.eligibility_status == EligibilityStatus.PCS_ELIGIBLE for r in rows),
                "timing_watch_count": sum(r.timing_status == TimingStatus.WATCH for r in rows),
                "timing_entry_ready_count": sum(r.timing_status == TimingStatus.TIMING_ENTRY_READY for r in rows),
                "options_check_count": sum(r.options_status != OptionsStatus.NOT_EVALUATED for r in rows),
                "spread_count": sum(r.spread_count for r in rows), "pcs_trade_ready_count": 0,
                "missing_ticker_decisions": 0,
            }, stage_latency_ms={"total": (perf_counter()-started)*1000})
            if request.output_directory:
                from .artifacts import persist_pool_artifacts
                persist_pool_artifacts(result, request.output_directory)
            return result
    return _timeout_result(request, spec, started, reason, detail)
