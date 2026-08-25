"""System-owned, resumable ticker onboarding orchestration.

This module owns stage progression and persistence.  Stage implementations are
injected so the engine can use the canonical PCS ingestion/readiness services
and remain straightforward to test with deterministic fixtures.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping


class OnboardingStage(StrEnum):
    SOURCE_DISCOVERY = "SOURCE_DISCOVERY"
    DAILY_READY = "DAILY_READY"
    OPTIONS_INGESTION = "OPTIONS_INGESTION"
    OPTIONS_VALIDATION = "OPTIONS_VALIDATION"
    MANIFEST_UPDATE = "MANIFEST_UPDATE"
    ROUTE_ACTIVATION = "ROUTE_ACTIVATION"
    CANONICAL_READINESS = "CANONICAL_READINESS"
    CONTRACT_SMOKE = "CONTRACT_SMOKE"
    LIFECYCLE_SMOKE = "LIFECYCLE_SMOKE"
    RESEARCH_READY = "RESEARCH_READY"


class OnboardingStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class FailureType(StrEnum):
    RETRYABLE_EXTERNAL = "RETRYABLE_EXTERNAL"
    RECOVERABLE_ENGINEERING = "RECOVERABLE_ENGINEERING"
    DATA_QUALITY_FAILURE = "DATA_QUALITY_FAILURE"
    NON_RECOVERABLE_EXTERNAL = "NON_RECOVERABLE_EXTERNAL"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"


@dataclass(frozen=True)
class StageResult:
    status: str = "PASS"
    failure_type: str | None = None
    failure_reason: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)


@dataclass
class OnboardingState:
    symbol: str
    stage: str = OnboardingStage.SOURCE_DISCOVERY
    status: str = OnboardingStatus.PENDING
    started_at: str | None = None
    completed_at: str | None = None
    source_version: str | None = None
    shards_total: int = 0
    shards_complete: int = 0
    rows_processed: int = 0
    rows_written: int = 0
    min_date: str | None = None
    max_date: str | None = None
    duplicate_count: int = 0
    conflict_count: int = 0
    failure_type: str | None = None
    failure_reason: str | None = None
    stage_status: dict[str, str] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)


STAGES = tuple(OnboardingStage)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


class OnboardingEngine:
    """Advance stages automatically, persist every transition, and resume safely."""

    def __init__(self, symbol: str, state_root: str | Path = "data/onboarding", *, max_retries: int = 3, backoff_seconds: float = 0.0):
        self.symbol = str(symbol).strip().upper()
        self.state_path = Path(state_root) / self.symbol / "state.json"
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))

    def load(self) -> OnboardingState:
        if not self.state_path.exists():
            state = OnboardingState(self.symbol)
            state.stage_status = {stage.value: OnboardingStatus.PENDING.value for stage in STAGES}
            return state
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        raw.pop("_schema", None)
        state = OnboardingState(**raw)
        state.stage_status = {stage.value: OnboardingStatus.PENDING.value for stage in STAGES} | state.stage_status
        return state

    def persist(self, state: OnboardingState) -> None:
        _atomic_write(self.state_path, {"_schema": "pcs.onboarding.v1", **asdict(state)})

    def run(self, handlers: Mapping[str | OnboardingStage, Callable[[OnboardingState], StageResult]]) -> OnboardingState:
        state = self.load()
        if state.status == OnboardingStatus.PASS and state.stage == OnboardingStage.RESEARCH_READY:
            return state
        state.started_at = state.started_at or _now()
        state.status = OnboardingStatus.RUNNING
        state.stage_status = {stage.value: OnboardingStatus.PENDING.value for stage in STAGES} | state.stage_status
        self.persist(state)
        start = STAGES.index(OnboardingStage(state.stage))
        for index, stage in enumerate(STAGES[start:], start=start):
            handler = handlers.get(stage) or handlers.get(stage.value)
            if handler is None:
                return self._fail(state, stage, FailureType.RECOVERABLE_ENGINEERING, "missing stage handler")
            state.stage = stage.value
            state.stage_status[stage.value] = OnboardingStatus.RUNNING.value
            self.persist(state)
            stage_started = time.perf_counter()
            result = self._execute(handler, state, stage)
            state.metrics[stage.value] = {
                **result.metrics,
                "elapsed_seconds": round(time.perf_counter() - stage_started, 6),
                "attempts": state.attempts.get(stage.value, 0),
            }
            state.reason_codes = result.reason_codes
            if result.status.upper() != "PASS":
                state.stage_status[stage.value] = OnboardingStatus.BLOCKED.value
                return self._fail(state, stage, result.failure_type or FailureType.RECOVERABLE_ENGINEERING, result.failure_reason or "stage failed")
            state.stage_status[stage.value] = OnboardingStatus.PASS.value
            state.status = OnboardingStatus.PASS if stage == OnboardingStage.RESEARCH_READY else OnboardingStatus.RUNNING
            state.failure_type = state.failure_reason = None
            if stage != OnboardingStage.RESEARCH_READY:
                state.stage = STAGES[index + 1].value
            self.persist(state)
        state.completed_at = _now()
        self.persist(state)
        return state

    def progress(self) -> dict[str, Any]:
        """Return machine-readable persisted progress for status UIs/agents."""
        state = self.load()
        current = state.metrics.get(state.stage, {})
        return {
            "symbol": state.symbol, "stage": state.stage, "status": state.status,
            "stage_status": state.stage_status,
            "shards": {"complete": state.shards_complete, "total": state.shards_total},
            "rows": {"processed": state.rows_processed, "written": state.rows_written},
            "duplicates": state.duplicate_count, "conflicts": state.conflict_count,
            "failure_type": state.failure_type, "failure_reason": state.failure_reason,
            "current_metrics": current, "attempts": state.attempts,
        }

    def _execute(self, handler: Callable[[OnboardingState], StageResult], state: OnboardingState, stage: OnboardingStage) -> StageResult:
        attempts = state.attempts.get(stage.value, 0)
        while True:
            attempts += 1
            state.attempts[stage.value] = attempts
            self.persist(state)
            try:
                result = handler(state)
            except Exception as exc:  # stage adapters classify expected failures explicitly
                detail = str(exc)
                failure = next((candidate for candidate in FailureType if detail.startswith(f"{candidate.value}:")), FailureType.RECOVERABLE_ENGINEERING)
                result = StageResult("FAIL", failure, detail)
            if result.status.upper() == "PASS" or result.failure_type != FailureType.RETRYABLE_EXTERNAL or attempts > self.max_retries:
                return result
            if self.backoff_seconds:
                time.sleep(self.backoff_seconds * attempts)

    def _fail(self, state: OnboardingState, stage: OnboardingStage, failure_type: str, reason: str) -> OnboardingState:
        state.stage = stage.value
        state.status = OnboardingStatus.BLOCKED
        state.stage_status[stage.value] = OnboardingStatus.BLOCKED.value
        state.failure_type = str(failure_type)
        state.failure_reason = reason
        self.persist(state)
        return state


__all__ = ["OnboardingEngine", "OnboardingStage", "OnboardingStatus", "FailureType", "StageResult", "OnboardingState"]
