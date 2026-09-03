"""Run-scoped runtime primitives for bounded, repeatable pool execution.

This module is an orchestration boundary only. It pins input evidence for one
run, coalesces concurrent handle/frame requests, and owns the executor for a
stage. It deliberately contains no market, strategy, or contract logic.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError, as_completed
from dataclasses import dataclass
from hashlib import sha256
import inspect
import json
from threading import RLock
from time import perf_counter
from typing import Any, Callable, Sequence, TypeVar

import pandas as pd

from .concurrency import WorkerOutcome


T = TypeVar("T")


def _json_value(value: Any) -> Any:
    """Return a stable scalar for manifest identity serialization."""
    if value is None:
        return None
    try:
        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


@dataclass(frozen=True)
class ManifestSnapshot:
    """Immutable manifest evidence captured once at pool-run start."""

    path: str
    columns: tuple[str, ...]
    rows: tuple[tuple[tuple[str, Any], ...], ...]
    identity: str

    @classmethod
    def capture(cls, access: Any) -> "ManifestSnapshot":
        path = str(getattr(access, "manifest_path", ""))
        reader = getattr(access, "_read_manifest", None)
        manifest = reader(access.manifest_path) if callable(reader) else pd.DataFrame()
        if not isinstance(manifest, pd.DataFrame):
            manifest = pd.DataFrame(manifest)
        columns = tuple(str(column) for column in manifest.columns)
        rows = tuple(
            tuple((str(key), _json_value(value)) for key, value in record.items())
            for record in manifest.to_dict("records")
        )
        payload = {"path": path, "columns": columns, "rows": rows}
        identity = sha256(json.dumps(
            payload, sort_keys=True, default=str, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        return cls(path, columns, rows, identity)

    def to_frame(self) -> pd.DataFrame:
        """Return a defensive tabular copy for a compatible resolver."""
        return pd.DataFrame([dict(row) for row in self.rows], columns=self.columns)


@dataclass(frozen=True)
class StageRun:
    """Ordered stage outcomes and elapsed wall-clock time."""

    outcomes: tuple[WorkerOutcome, ...]
    elapsed_ms: float


class PoolRuntime:
    """Run-local caches, single-flight coordination, and stage execution.

    A cached frame is private to the runtime. Public read methods always
    return a deep copy, so an indicator helper cannot mutate another worker's
    input or the cached snapshot.
    """

    def __init__(self, *, access: Any | None = None, run_id: str = "",
                 as_of: str = "", max_workers: int = 8,
                 stage_timeout_seconds: float | None = 60.0,
                 daily_handle_resolver: Callable[..., Any] | None = None,
                 options_handle_resolver: Callable[..., Any] | None = None):
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if stage_timeout_seconds is not None and stage_timeout_seconds <= 0:
            raise ValueError("stage_timeout_seconds must be positive")
        self.access = access
        self.run_id = str(run_id)
        self.as_of = str(as_of)
        self.max_workers = int(max_workers)
        self.stage_timeout_seconds = stage_timeout_seconds
        self.daily_handle_resolver = daily_handle_resolver
        self.options_handle_resolver = options_handle_resolver
        self.manifest_snapshot = ManifestSnapshot.capture(access) if access is not None else None
        self.stage_latency_ms: dict[str, float] = {}
        self.counters: dict[str, int] = {
            "handle_resolution_calls": 0,
            "daily_frame_reads": 0,
            "options_frame_reads": 0,
        }
        self._lock = RLock()
        self._inflight: dict[tuple[Any, ...], Future[Any]] = {}
        self._values: dict[tuple[Any, ...], Any] = {}
        # Kept as an internal compatibility alias for the original runtime
        # cache, while frames remain in their own namespace.
        self._handles = self._values
        self._frames: dict[tuple[Any, ...], pd.DataFrame] = {}

    @property
    def manifest_snapshot_id(self) -> str:
        return self.manifest_snapshot.identity if self.manifest_snapshot else ""

    def refresh_manifest_snapshot(self) -> None:
        """Refresh the pinned boundary after an authorized preparation write."""
        with self._lock:
            self.manifest_snapshot = ManifestSnapshot.capture(self.access)

    @staticmethod
    def _handle_key(handle: Any) -> tuple[Any, ...]:
        return (
            str(getattr(handle, "dataset", "")),
            str(getattr(handle, "ticker", "")).upper(),
            str(getattr(handle, "generation_id", "")),
            str(getattr(handle, "checksum", "")),
            str(getattr(handle, "dataset_fingerprint", "")),
        )

    def _single_flight(self, key: tuple[Any, ...], producer: Callable[[], T]) -> T:
        """Produce one value per key, sharing the in-flight Future."""
        with self._lock:
            if key in self._values:
                return self._values[key]
            if key in self._frames:
                return self._frames[key]  # type: ignore[return-value]
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._inflight[key] = future
        if not owner:
            return future.result()  # type: ignore[union-attr,return-value]
        try:
            value = producer()
        except BaseException as exc:
            future.set_exception(exc)  # type: ignore[union-attr]
            with self._lock:
                self._inflight.pop(key, None)
            raise
        with self._lock:
            if isinstance(value, pd.DataFrame):
                self._frames[key] = value.copy(deep=True)
            else:
                self._values[key] = value
            self._inflight.pop(key, None)
        future.set_result(value)  # type: ignore[union-attr]
        return value

    def resolve_handle(self, key: tuple[Any, ...], resolver: Callable[[], Any]) -> Any:
        """Backward-compatible generic single-flight handle resolver."""
        return self._single_flight(key, resolver)

    @staticmethod
    def _call_with_snapshot(resolver: Callable[..., Any], *args: Any,
                            data_access: Any, snapshot: ManifestSnapshot | None) -> Any:
        """Supply the snapshot only to resolvers that explicitly accept it."""
        try:
            parameters = inspect.signature(resolver).parameters
        except (TypeError, ValueError):
            parameters = {}
        kwargs = {"data_access": data_access}
        if snapshot is not None and "manifest_snapshot" in parameters:
            kwargs["manifest_snapshot"] = snapshot
        return resolver(*args, **kwargs)

    def resolve_daily_handle(self, symbol: str, as_of: Any, warmup: int, *,
                             resolver: Callable[..., Any] | None = None,
                             prepare: Callable[[], Any] | None = None,
                             auto_prepare: bool = False) -> Any:
        normalized = str(symbol).strip().upper()
        # A pinned handle is complete for the run; end_date is applied when a
        # frame is read. Omitting it here coalesces benchmark and ticker asks.
        key = ("daily_handle", normalized, int(warmup))
        resolver = resolver or self.daily_handle_resolver
        if resolver is None:
            raise ValueError("DAILY_HANDLE_RESOLVER_MISSING")

        def produce() -> Any:
            started = perf_counter()
            with self._lock:
                self.counters["handle_resolution_calls"] += 1
            try:
                value = self._call_with_snapshot(
                    resolver, normalized, as_of, warmup,
                    data_access=self.access, snapshot=self.manifest_snapshot,
                )
            except Exception:
                if not auto_prepare or prepare is None:
                    raise
                prepare()
                value = self._call_with_snapshot(
                    resolver, normalized, as_of, warmup,
                    data_access=self.access, snapshot=self.manifest_snapshot,
                )
            with self._lock:
                self.stage_latency_ms["handle_resolution"] = (
                    self.stage_latency_ms.get("handle_resolution", 0.0)
                    + (perf_counter() - started) * 1000
                )
            return value

        return self._single_flight(key, produce)

    def resolve_daily(self, symbol: str, as_of: Any, warmup: int, *,
                      resolver: Callable[..., Any] | None = None) -> Any:
        """Public compatibility alias for the pinned daily resolver."""
        return self.resolve_daily_handle(symbol, as_of, warmup, resolver=resolver)

    def read_daily(self, handle: Any, *, end_date: Any = None,
                   required_warmup_rows: int = 0) -> pd.DataFrame:
        key = ("daily_frame",) + self._handle_key(handle)

        def produce() -> pd.DataFrame:
            with self._lock:
                self.counters["daily_frame_reads"] += 1
            return self.access.read_verified_dataset(
                handle, required_warmup_rows=required_warmup_rows
            )

        frame = self._single_flight(key, produce)
        out = frame.copy(deep=True)
        if end_date is not None and "date" in out.columns:
            cutoff = pd.Timestamp(end_date)
            dates = pd.to_datetime(out["date"], errors="coerce")
            try:
                if getattr(dates.dt, "tz", None) is not None and cutoff.tzinfo is None:
                    cutoff = cutoff.tz_localize(dates.dt.tz)
            except (AttributeError, TypeError):
                pass
            out = out[dates <= cutoff]
        return out.reset_index(drop=True)

    def read_options(self, symbol: str, trade_date: Any, *,
                     reader: Callable[..., Any] | None) -> pd.DataFrame:
        normalized = str(symbol).strip().upper()
        day = pd.Timestamp(trade_date).normalize()
        key = ("options_frame", normalized, str(day.date()), id(reader))

        def produce() -> pd.DataFrame:
            with self._lock:
                self.counters["options_frame_reads"] += 1
            if reader is None:
                raise ValueError("OPTIONS_READER_MISSING")
            return reader(normalized, day)

        frame = self._single_flight(key, produce)
        return frame.copy(deep=True).reset_index(drop=True)

    def read_options_handle(self, handle: Any, *, start_date: Any = None, end_date: Any = None) -> pd.DataFrame:
        """Read one already-verified, generation-pinned options handle."""
        key = ("options_handle_frame",) + self._handle_key(handle) + (str(start_date), str(end_date))
        def produce() -> pd.DataFrame:
            with self._lock:
                self.counters["options_frame_reads"] += 1
            return self.access.read_verified_dataset(handle, start_date=start_date, end_date=end_date)
        return self._single_flight(key, produce).copy(deep=True).reset_index(drop=True)

    def run_stage(self, symbols: Sequence[str], worker: Callable[[str], T], *,
                  stage_name: str = "stage", max_workers: int | None = None,
                  timeout_seconds: float | None = None) -> StageRun:
        """Run one ordered stage with one executor and a bounded deadline."""
        normalized = tuple(str(symbol).strip().upper() for symbol in symbols)
        if len(normalized) != len(set(normalized)):
            raise ValueError("DUPLICATE_WORKER_SYMBOL")
        if not normalized:
            self.stage_latency_ms[stage_name] = 0.0
            return StageRun((), 0.0)
        workers = min(int(max_workers or self.max_workers), len(normalized))
        if workers < 1:
            raise ValueError("max_workers must be positive")
        timeout = self.stage_timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        started = perf_counter()
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pcs-pool")
        futures = {executor.submit(worker, symbol): symbol for symbol in normalized}
        outcomes: dict[str, WorkerOutcome] = {}
        try:
            for future in as_completed(futures, timeout=timeout):
                symbol = futures[future]
                try:
                    outcomes[symbol] = WorkerOutcome(symbol, future.result())
                except Exception as exc:
                    outcomes[symbol] = WorkerOutcome(
                        symbol, reason_codes=("WORKER_FAILED", type(exc).__name__)
                    )
        except TimeoutError:
            pass
        finally:
            for future, symbol in futures.items():
                if symbol in outcomes:
                    continue
                future.cancel()
                outcomes[symbol] = WorkerOutcome(symbol, reason_codes=("WORKER_TIMEOUT",))
            # A context manager would wait for a slow worker after the timeout.
            executor.shutdown(wait=False, cancel_futures=True)
        elapsed = (perf_counter() - started) * 1000
        self.stage_latency_ms[stage_name] = elapsed
        return StageRun(tuple(outcomes[symbol] for symbol in normalized), elapsed)


__all__ = ["ManifestSnapshot", "PoolRuntime", "StageRun"]
