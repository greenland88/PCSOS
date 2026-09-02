"""Failure-isolated, deterministically ordered pool worker execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from dataclasses import dataclass
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class WorkerOutcome:
    symbol: str
    value: object | None = None
    reason_codes: tuple[str, ...] = ()


def run_symbol_workers(symbols: Sequence[str], worker: Callable[[str], R], *, max_workers: int = 8,
                       timeout_seconds: float | None = None) -> tuple[WorkerOutcome, ...]:
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    normalized = tuple(str(symbol).strip().upper() for symbol in symbols)
    if len(normalized) != len(set(normalized)):
        raise ValueError("DUPLICATE_WORKER_SYMBOL")
    outcomes: dict[str, WorkerOutcome] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(normalized) or 1)) as executor:
        futures = {executor.submit(worker, symbol): symbol for symbol in normalized}
        try:
            completed = as_completed(futures, timeout=timeout_seconds)
            for future in completed:
                symbol = futures[future]
                try:
                    outcomes[symbol] = WorkerOutcome(symbol, future.result())
                except Exception as exc:
                    outcomes[symbol] = WorkerOutcome(symbol, reason_codes=("WORKER_FAILED", type(exc).__name__))
        except TimeoutError:
            for future, symbol in futures.items():
                if symbol not in outcomes:
                    future.cancel()
                    outcomes[symbol] = WorkerOutcome(symbol, reason_codes=("WORKER_TIMEOUT",))
    return tuple(outcomes[symbol] for symbol in normalized)
