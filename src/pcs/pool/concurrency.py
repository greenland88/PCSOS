"""Failure-isolated, deterministically ordered pool worker execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class WorkerOutcome:
    symbol: str
    value: object | None = None
    reason_codes: tuple[str, ...] = ()


def run_symbol_workers(symbols: Sequence[str], worker: Callable[[str], R], *, max_workers: int = 8,
                       timeout_seconds: float | None = 60.0,
                       include_error_details: bool = False) -> tuple[WorkerOutcome, ...]:
    """Bound result collection, preserving order and not starting late work.

    Python threads cannot interrupt a running callable. This API cancels queued
    work and stops waiting at the deadline; callers must bound blocking I/O.
    The read-only CLI adds process isolation for a hard execution deadline.
    ``None`` retains the finite default rather than disabling the deadline.
    """
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    timeout = 60.0 if timeout_seconds is None else float(timeout_seconds)
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    normalized = tuple(str(symbol).strip().upper() for symbol in symbols)
    if len(normalized) != len(set(normalized)):
        raise ValueError("DUPLICATE_WORKER_SYMBOL")
    if not normalized:
        return ()
    workers = min(max_workers, len(normalized))
    outcomes: dict[str, WorkerOutcome] = {}
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pcs-pool")
    deadline = perf_counter() + timeout
    futures = {}
    pending = iter(normalized)
    not_started = set(normalized)
    try:
        for _ in range(workers):
            symbol = next(pending)
            futures[executor.submit(worker, symbol)] = symbol
            not_started.remove(symbol)
        while futures:
            remaining = max(0.0, deadline - perf_counter())
            done, _ = wait(tuple(futures), timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                symbol = futures.pop(future)
                try:
                    outcomes[symbol] = WorkerOutcome(symbol, future.result())
                except Exception as exc:
                    reasons = ("WORKER_FAILED", type(exc).__name__)
                    if include_error_details:
                        reasons += (str(exc) or type(exc).__name__,)
                    outcomes[symbol] = WorkerOutcome(symbol, reason_codes=reasons)
                if perf_counter() < deadline:
                    next_symbol = next(pending, None)
                    if next_symbol is not None:
                        futures[executor.submit(worker, next_symbol)] = next_symbol
                        not_started.remove(next_symbol)
    finally:
        for future, symbol in futures.items():
            future.cancel()
            outcomes[symbol] = WorkerOutcome(symbol, reason_codes=("WORKER_TIMEOUT",))
        for symbol in not_started:
            outcomes[symbol] = WorkerOutcome(symbol, reason_codes=("STAGE_DEADLINE_NOT_STARTED",))
        executor.shutdown(wait=False, cancel_futures=True)
    return tuple(outcomes[symbol] for symbol in normalized)
