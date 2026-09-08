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


class SupportZonePolicy(StrictModel):
    policy_id: str = "support-zones-research-v1"
    schema_version: Literal["1.0"] = "1.0"
    calculation_version: Literal["support-zones-v1", "support-zones-v2"] = "support-zones-v2"
    analysis_sessions: int = Field(default=60, ge=7, le=252)
    indicator_warmup_sessions: int = Field(default=200, ge=50, le=1000)
    pivot_left_bars: Literal[3] = 3
    pivot_right_bars: Literal[3] = 3
    atr_period: Literal[14] = 14
    zone_width_atr: float = Field(default=0.35, gt=0)
    break_buffer_atr: float = Field(default=0.35, gt=0)
    held_rebound_atr: float = Field(default=0.5, gt=0)
    retest_departure_atr: float = Field(default=0.5, gt=0)
    confirmation_sessions: Literal[3] = 3
    parameter_source: Literal["DEFAULT", "REQUEST"] = "DEFAULT"

    @model_validator(mode="before")
    @classmethod
    def mark_support_overrides(cls, values):
        if isinstance(values, dict):
            values = dict(values)
            if any(k in values and values[k] != f.default for k, f in cls.model_fields.items()
                   if k != "parameter_source"):
                values["parameter_source"] = "REQUEST"
        return values

    @property
    def required_sessions(self) -> int:
        return self.analysis_sessions + self.indicator_warmup_sessions


class SupportFeatureBar(StrictModel):
    session: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    sma20: float | None
    sma50: float | None
    atr14: float | None


class ConfirmedSwingEvidence(StrictModel):
    source_id: str
    pivot_date: str
    confirmed_at: str
    swing_type: Literal["low", "high"]
    price: float


class SupportFeatureView(StrictModel):
    symbol: str
    bars: list[SupportFeatureBar]
    confirmed_swings: list[ConfirmedSwingEvidence]
    expected_sessions: list[str]
    analysis_start: str
    indicator_seed_start: str
    indicator_identity: str
    source: SourceReference
    price_basis: str
    corporate_action_version: str
    input_kind: Literal["VERIFIED_CANONICAL", "TEST"]
    source_timestamp: str | None = None
    received_at: str | None = None


class SupportSourceAnchor(StrictModel):
    source_id: str
    source_type: Literal["SMA20", "SMA50", "CONFIRMED_SWING_LOW"]
    price: float
    observed_at: str
    available_at: str
    pivot_date: str | None = None
    retrospective: bool = False


class SupportTestEvent(StrictModel):
    test_id: str
    touch_session: str
    touch_low: float
    touch_high: float
    cumulative_low: float
    confirmation_deadline: str
    status: Literal["IN_PROGRESS", "HELD", "UNCONFIRMED_TEST"]
    first_held_at: str | None = None
    ended_at: str | None = None
    departure_session: str | None = None
    rebound_atr: float | None = None
    penetration_atr: float
    reason_codes: list[str]


class SupportIntradayBreach(StrictModel):
    session: str
    low: float
    invalidation_line: float
    penetration_atr: float


class SupportZone(StrictModel):
    zone_id: str
    symbol: str
    zone_type: Literal["MA_REFERENCE", "SWING_LOW", "CONFLUENCE"]
    lower: float
    upper: float
    anchor_price: float
    anchor_atr: float
    invalidation_line: float
    formed_at: str
    available_at: str
    creation_sources: list[SupportSourceAnchor]
    observed_source_ids: list[str]
    observed_sources: list[SupportSourceAnchor] | None = None
    policy_sha256: str | None = None
    price_basis: str
    corporate_action_version: str
    policy_id: str
    calculation_version: str
    state: Literal["REFERENCE_ONLY", "TEST_IN_PROGRESS", "SINGLE_HELD_TEST", "REPEATED_HELD_TESTS", "BROKEN"]
    evidence_grade: Literal["REFERENCE", "IN_PROGRESS", "SINGLE_HELD", "REPEATED_HELD", "BROKEN"]
    tests: list[SupportTestEvent]
    intraday_breaches: list[SupportIntradayBreach]
    broken_at: str | None = None
    broken_close: float | None = None
    bound: bool = False
    active: bool = True
    archived_at: str | None = None
    archive_reason: str | None = None
    reason_codes: list[str]


class SupportHistoryRecord(StrictModel):
    history_id: str
    session: str
    zone_id: str
    event_type: Literal["ZONE_FORMED", "ZONE_ARCHIVED", "SOURCE_RESONANCE", "RETROSPECTIVE_INTERSECTION", "DAILY_STATE", "TEST_STARTED", "TEST_UPDATED", "TEST_HELD", "TEST_UNCONFIRMED", "DEPARTED", "INTRADAY_PENETRATION", "BROKEN", "DATA_MISSING", "PRIOR_STATE_INVALIDATED"]
    known_at: str
    retrospective: bool
    close: float | None = None
    low: float | None = None
    high: float | None = None
    zone_lower: float
    zone_upper: float
    invalidation_line: float
    anchor_atr: float
    zone_state: str
    test_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str]


class SupportZoneState(StrictModel):
    symbol: str
    zones: list[SupportZone]
    support_history: list[SupportHistoryRecord]
    evaluated_through: str | None
    state_revision: int
    input_prefix_sha256: str
    source_identity: str
    price_basis: str
    corporate_action_version: str
    policy_sha256: str
    indicator_identity: str


class SupportZoneInput(StrictModel):
    call_context: CallContext
    feature_view: SupportFeatureView
    effective_policy: SupportZonePolicy = Field(default_factory=SupportZonePolicy)
    prior_state: SupportZoneState | None = None
    bound_zone_id: str | None = None


class SupportZoneCoverage(StrictModel):
    expected_sessions: list[str]
    actual_sessions: list[str]
    missing_sessions: list[str]
    analysis_start: str
    evaluated_through: str | None
    indicator_seed_start: str
    indicator_identity: str
    legal_input_sha256: str
    source_identity: str
    fields: dict[str, CapabilityStatus]
    reason_codes: list[str]


class SupportZoneSelection(StrictModel):
    role: Literal["RECENT_OBSERVED_SUPPORT", "CANDIDATE_KEY_SUPPORT", "BOUND_SUPPORT"]
    zone_id: str | None
    status: Literal["SELECTED", "UNBOUND", "UNAVAILABLE"]
    reason_codes: list[str]


class SupportZoneResult(StrictModel):
    module: str = "support_zones"
    version: Literal["1.0", "1.1"] = "1.1"
    symbol: str
    as_of: str
    status: CapabilityStatus
    data_timestamp: str | None
    received_at: str | None
    calculation_version: str = "support-zones-v2"
    run_id: str
    request_id: str
    result_id: str
    call_diagnostics: list[Literal["PRIOR_STATE_INVALIDATED_REPLAYED"]] = Field(default_factory=list)
    reason_codes: list[str]
    call_context: CallContext
    effective_policy: SupportZonePolicy
    policy_sha256: str
    current_zones: list[SupportZone]
    archived_zones: list[SupportZone]
    support_history: list[SupportHistoryRecord]
    state_changes: list[SupportHistoryRecord]
    selections: list[SupportZoneSelection]
    unselected_zones: list[dict]
    coverage: SupportZoneCoverage
    next_state: SupportZoneState
    provenance: list[SourceReference]
    explanation: str
