"""Run-scoped, thread-safe caches for the production pool runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from time import perf_counter
from typing import Any, Callable


@dataclass
class PoolRuntime:
    """Owns immutable run inputs and de-duplicates handle resolution per run."""
    manifest_snapshot: Any = None
    _handles: dict[tuple[Any, ...], Any] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock, repr=False)
    stage_latency_ms: dict[str, float] = field(default_factory=dict)

    def resolve_handle(self, key: tuple[Any, ...], resolver: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._handles:
                return self._handles[key]
        started = perf_counter()
        value = resolver()
        with self._lock:
            # A concurrent resolver may have won; all callers receive the same
            # canonical object for this run.
            value = self._handles.setdefault(key, value)
            self.stage_latency_ms["handle_resolution"] = self.stage_latency_ms.get("handle_resolution", 0.0) + (perf_counter() - started) * 1000
            return value

