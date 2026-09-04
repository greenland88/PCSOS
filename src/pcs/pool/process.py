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
    thread keeps the child alive. No provider preparation or output writes are
    accepted in this request schema.
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
    try:
        process.start()
        launched = True
        sender.close()
        remaining = max(0.0, timeout_seconds - (perf_counter() - started))
        if receiver.poll(remaining):
            try:
                kind, payload = receiver.recv()
            except EOFError:
                pass
            else:
                if kind == "result" and isinstance(payload, PoolScanResult):
                    from .validation import validate_pool_result
                    validate_pool_result(payload, spec.symbols)
                    return payload
                detail = str(payload)
        else:
            reason = "POOL_SCAN_TIMEOUT"
            detail = "read-only scan exceeded its process deadline"
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
