"""Stable, JSON-serializable contracts for the PCS pool funnel."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping
import json


class EligibilityStatus(StrEnum):
    HARD_EXCLUDED = "HARD_EXCLUDED"
    DATA_BLOCKED = "DATA_BLOCKED"
    TEMP_INELIGIBLE = "TEMP_INELIGIBLE"
    PCS_ELIGIBLE = "PCS_ELIGIBLE"


class TimingStatus(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    DORMANT = "DORMANT"
    WATCH = "WATCH"
    TIMING_ENTRY_READY = "TIMING_ENTRY_READY"
    WAIT = "WAIT"


class OptionsStatus(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    PASS = "PASS"
    REJECT = "REJECT"
    DATA_BLOCKED = "DATA_BLOCKED"


class FinalAction(StrEnum):
    PCS_TRADE_READY = "PCS_TRADE_READY"
    WATCH = "WATCH"
    WAIT = "WAIT"
    TEMP_BLOCKED = "TEMP_BLOCKED"
    REJECTED = "REJECTED"
    DATA_FAILED = "DATA_FAILED"


@dataclass(frozen=True)
class PoolRunSnapshot:
    run_id: str
    as_of: str
    mode: str
    last_completed_daily_session: str | None
    universe_snapshot_id: str
    benchmark_handles: Mapping[str, Any] = field(default_factory=dict)
    symbol_handles: Mapping[str, Any] = field(default_factory=dict)
    profile_versions: Mapping[str, str] = field(default_factory=dict)
    engine_version: str = "unknown"
    code_revision: str = "unknown"
    refresh_policy: str = "FULL"
    manifest_snapshot_id: str = ""
    requested_as_of: str = ""
    effective_daily_session: str | None = None


@dataclass(frozen=True)
class TickerScanResult:
    symbol: str
    run_id: str
    as_of: str
    eligibility_status: EligibilityStatus
    timing_status: TimingStatus = TimingStatus.NOT_EVALUATED
    options_status: OptionsStatus = OptionsStatus.NOT_EVALUATED
    event_status: str = "NOT_EVALUATED"
    portfolio_status: str = "NOT_EVALUATED"
    final_action: FinalAction = FinalAction.DATA_FAILED
    reason_codes: tuple[str, ...] = ()
    next_review_at: str | None = None
    reentry_conditions: tuple[str, ...] = ()
    generation_id: str | None = None
    dataset_fingerprint: str | None = None
    profile_version: str | None = None
    feature_max_date: str | None = None
    timeframe: str = "daily"
    latency_ms: float = 0.0
    spread_count: int = 0


@dataclass(frozen=True)
class PoolScanResult:
    snapshot: PoolRunSnapshot
    ticker_results: tuple[TickerScanResult, ...]
    summary: Mapping[str, int] = field(default_factory=dict)
    stage_latency_ms: Mapping[str, float] = field(default_factory=dict)
    counters: Mapping[str, int] = field(default_factory=lambda: {
        "ordinary_reader_calls": 0, "options_reader_calls": 0,
        "provider_calls": 0, "promotion_calls": 0, "recovery_calls": 0,
    })

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, sort_keys=True)
