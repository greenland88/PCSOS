"""Versioned value objects for descriptive stock analysis (no trading decisions)."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from pcs.analysis_contracts import (
    CallContext, CapabilityStatus, EvidenceValue, SourceReference, StrictModel,
)


class ProfilePolicy(StrictModel):
    policy_id: str = "underlying-profile-descriptive-v1"
    schema_version: Literal["1.0"] = "1.0"
    calculation_version: Literal["underlying-profile-v1"] = "underlying-profile-v1"
    analysis_sessions: int = Field(default=252, ge=60, le=2520)
    atr_period: int = Field(default=14, ge=2, le=100)
    peak_lookback: int = Field(default=60, ge=2, le=252)
    episode_threshold: float = Field(default=0.05, gt=0, lt=1)
    annualization_sessions: int = Field(default=252, ge=1)
    parameter_source: Literal["DEFAULT", "REQUEST"] = "DEFAULT"

    @model_validator(mode="before")
    @classmethod
    def mark_parameter_overrides(cls, values):
        if isinstance(values, dict):
            values = dict(values)
            overrides = any(k in values and values[k] != f.default
                            for k, f in cls.model_fields.items() if k != "parameter_source")
            if overrides:
                values["parameter_source"] = "REQUEST"
        return values

    @property
    def required_sessions(self) -> int:
        # Prefix for the first peak, plus an ATR seed before that prefix.
        return self.analysis_sessions + self.peak_lookback + self.atr_period


class DailyBar(StrictModel):
    session: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None


class DailyFeatureView(StrictModel):
    symbol: str
    bars: list[DailyBar]
    source: SourceReference
    price_basis: str
    corporate_action_version: str
    currency: str | None = None
    source_timestamp: str | None = None
    received_at: str | None = None
    input_kind: Literal["VERIFIED_CANONICAL", "TEST"]


class ProfileInput(StrictModel):
    call_context: CallContext
    feature_view: DailyFeatureView
    benchmark: DailyFeatureView | None = None
    effective_policy: ProfilePolicy = Field(default_factory=ProfilePolicy)
    calendar: str = "XNYS"
    benchmark_reason_codes: list[str] = Field(default_factory=list)


class ProfileMetric(EvidenceValue):
    status: CapabilityStatus
    formula: str
    as_of: str
    window_start: str
    window_end: str
    coverage: float
    parameters: dict[str, float | int | str]
    reason_codes: list[str]


class DrawdownEpisode(StrictModel):
    episode_id: str
    start_session: str | None
    first_observed_session: str
    frozen_peak_session: str
    frozen_peak_close: float
    peak_history_complete: bool
    trough_session: str
    trough_close: float
    max_depth: float
    recovered: bool
    recovery_session: str | None
    observed_sessions: int
    recovery_sessions: int | None
    left_truncated: bool
    right_censored: bool
    censor_session: str | None
    reason_codes: list[str]


class GapObservation(StrictModel):
    session: str
    previous_session: str
    previous_close: float | None
    open: float | None
    atr_previous_session: float | None
    up_gap_atr: float | None
    down_gap_atr: float | None
    reason_codes: list[str]


class DrawdownObservation(StrictModel):
    session: str
    close: float | None
    reference_peak_close: float | None
    depth: float | None


class SurvivalPoint(StrictModel):
    elapsed_sessions: int
    at_risk: int
    recoveries: int
    censored: int
    survival: float


class ProfileCoverage(StrictModel):
    calendar: str
    requested_sessions: list[str]
    analysis_sessions: list[str]
    actual_sessions: list[str]
    missing_sessions: list[str]
    missing_analysis_sessions: list[str]
    prefix_required: int
    prefix_available: int
    indicator_seed_start: str | None
    atr_segment_seeds: list[str]
    legal_input_sha256: str
    benchmark_input_sha256: str | None
    invalid_fields: dict[str, list[str]]


class ProfileTimeContext(StrictModel):
    requested_as_of: str
    effective_daily_session: str
    calendar: str
    exchange_timezone: str
    completed_daily_bar: bool
    known_at_lower_bound: str
    source_timestamp: str | None
    received_at: str | None


class UnderlyingProfile(StrictModel):
    module: str = "underlying_profile"
    version: Literal["1.0"] = "1.0"
    symbol: str
    as_of: str
    status: CapabilityStatus
    profile_status: CapabilityStatus
    data_timestamp: str | None
    calculation_version: str = "underlying-profile-v1"
    run_id: str
    request_id: str
    result_id: str
    reason_codes: list[str]
    call_context: CallContext
    time_context: ProfileTimeContext
    effective_policy: ProfilePolicy
    policy_sha256: str
    measurements: list[ProfileMetric]
    episodes: list[DrawdownEpisode]
    gap_observations: list[GapObservation]
    drawdown_observations: list[DrawdownObservation]
    recovery_survival: list[SurvivalPoint]
    coverage: ProfileCoverage
    provenance: list[SourceReference]
    explanation: str
