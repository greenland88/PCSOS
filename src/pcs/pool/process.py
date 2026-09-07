"""Process lifetime boundary for read-only CLI scans of the canonical runner.

Never use this supervisor for import/promotion work: terminating a writer could
interrupt its transaction. Python API stage timeouts only bound result collection.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import multiprocessing
from time import perf_counter
from typing import Any
import uuid

from .models import EligibilityStatus, PoolRunSnapshot, PoolScanResult, TickerScanResult
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
    resume: bool = True
    new_run: bool = False
    resume_run_id: str | None = None


def _scan_worker(request: ReadOnlyScanRequest, sender: Any) -> None:
    try:
        from pcs.data.access import PCSDataAccess
        from .options import load_pool_option_rules
        from .runner import run_pcs_pool

        result = run_pcs_pool(
            symbols=request.symbols, universe_id=request.universe_id,
            as_of=request.as_of, mode=request.mode, data_mode="READ_ONLY",
            auto_prepare_data=False, max_workers=request.max_workers,
            stage_timeout_seconds=request.stage_timeout_seconds,
            data_access=PCSDataAccess(manifest_path=request.manifest_path,
                                      parquet_root=request.parquet_root),
            option_rules=load_pool_option_rules(request.rules),
            output_directory=request.output_directory,
            resume=request.resume,
            new_run=request.new_run, resume_run_id=request.resume_run_id,
            checkpoint_callback=lambda path, identity: sender.send(("checkpoint", (path, identity))),
        )
        sender.send(("result", result))
    except Exception as exc:
        sender.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        sender.close()


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
    run_id = uuid.uuid4().hex
    snapshot = PoolRunSnapshot(run_id, request.as_of, request.mode, None,
                               f"{spec.universe_id}:{spec.version}:{spec.fingerprint}",
                               requested_as_of=request.as_of)
    rows = tuple(TickerScanResult(symbol, run_id, request.as_of, EligibilityStatus.DATA_BLOCKED,
                                  reason_codes=(reason,), warnings=(detail,)) for symbol in spec.symbols)
    return PoolScanResult(snapshot, rows, summary={
        "raw_count": len(rows), "data_blocked_count": len(rows),
        "missing_ticker_decisions": 0, "spread_count": 0, "pcs_trade_ready_count": 0,
        "run_status": "PARTIAL_TIMEOUT" if reason == "POOL_SCAN_TIMEOUT" else "FAILED",
    }, stage_latency_ms={"total": (perf_counter() - started) * 1000})
