"""Versioned value objects for descriptive stock analysis (no trading decisions)."""
from __future__ import annotations

from datetime import date
from enum import StrEnum
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


# Step 4: reusable entry-opportunity contracts.  These are additive; the
# legacy OpportunitySnapshot contract in opportunity_engine.py remains intact.
class OpportunityStateName(StrEnum):
    NO_SETUP = "NO_SETUP"
    WATCH = "WATCH"
    CONFIRMING = "CONFIRMING"
    ENTRY_READY = "ENTRY_READY"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class OpportunityPolicy(StrictModel):
    policy_id: str = "healthy-pullback-opportunity-v1.7"
    schema_version: Literal["1.0"] = "1.0"
    calculation_version: Literal["entry-opportunity-v2"] = "entry-opportunity-v2"
    family: Literal["HEALTHY_PULLBACK"] = "HEALTHY_PULLBACK"
    analysis_sessions: int = Field(default=60, ge=20, le=252)
    indicator_warmup_sessions: int = Field(default=200, ge=50, le=1000)
    recent_high_sessions: Literal[20] = 20
    healthy_pullback_min_pct: float = Field(default=0.05, ge=0, le=1)
    healthy_pullback_max_pct: float = Field(default=0.15, gt=0, le=1)
    shallow_pullback_max_pct: float = Field(default=0.05, ge=0, le=1)
    sma20_near_atr: float = Field(default=1.5, ge=0)
    sma50_near_atr: float = Field(default=2.0, ge=0)
    reclaim_buffer_atr: float = Field(default=0.10, ge=0)
    confirmation_sessions: Literal[3] = 3
    entry_window_sessions: Literal[3] = 3
    minimum_close_location: float = Field(default=0.55, ge=0, le=1)
    minimum_rvol20: float = Field(default=0.80, ge=0)
    maximum_entry_distance_atr: float = Field(default=1.75, ge=0)
    upper_wick_rejection_atr: float = Field(default=0.50, ge=0)
    upper_rejection_close_location: float = Field(default=0.35, ge=0, le=1)
    upper_wick_role: Literal["CONFIRMATION_BLOCKER"] = "CONFIRMATION_BLOCKER"
    parameter_source: Literal["PLAN_V1_7", "REQUEST"] = "PLAN_V1_7"

    @model_validator(mode="before")
    @classmethod
    def mark_opportunity_overrides(cls, values):
        if isinstance(values, dict):
            values = dict(values)
            ignored = {"parameter_source", "schema_version", "calculation_version", "family"}
            if any(k in values and k not in ignored and values[k] != f.default
                   for k, f in cls.model_fields.items()):
                values["parameter_source"] = "REQUEST"
        return values

    @property
    def required_sessions(self) -> int:
        return self.analysis_sessions + self.indicator_warmup_sessions


class OpportunityFeatureBar(StrictModel):
    session: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    sma20: float | None
    sma50: float | None
    sma200: float | None = None
    ema200: float | None = None
    atr14: float | None
    rsi14: float | None = None
    structure_state: Literal["bullish", "neutral", "deteriorating", "bearish"] | None = None
    trend_health: str | None = None
    legacy_pullback_state: str | None = None
    legacy_pullback_reasons: list[str] = Field(default_factory=list)


class OpportunityFeatureView(StrictModel):
    symbol: str
    bars: list[OpportunityFeatureBar]
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


class OpportunitySupportFact(StrictModel):
    session: str
    support_result_id: str
    zone_id: str
    test_id: str
    zone_lower: float
    zone_upper: float
    anchor_atr: float
    invalidation_line: float
    zone_available_at: str
    touch_session: str
    test_status: Literal["IN_PROGRESS", "HELD", "UNCONFIRMED_TEST"]
    first_held_at: str | None = None
    broken_at: str | None = None
    zone_state: str
    source_ids: list[str] = Field(default_factory=list)
    sources: list[SupportSourceAnchor] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class OpportunityCondition(StrictModel):
    condition_id: str
    session: str
    role: Literal["DISCOVERY", "CONFIRMATION", "INVALIDATION", "CURRENT_ELIGIBILITY", "DIAGNOSTIC"]
    left_value: float | str | bool | None = None
    operator: str | None = None
    right_value: float | str | bool | None = None
    unit: str | None = None
    predicate_value: bool | None = None
    status: Literal["EVALUATED", "UNKNOWN", "NOT_EVALUATED"]
    source_refs: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class OpportunityDetection(StrictModel):
    session: str
    detected: bool | None
    family: str
    recent_high: float | None
    recent_high_session: str | None
    pullback_pct: float | None
    distance_sma20_atr: float | None
    distance_sma50_atr: float | None
    legacy_pullback_state: str | None
    legacy_pullback_reasons: list[str]
    selected_support: OpportunitySupportFact | None
    alternative_supports: list[OpportunitySupportFact]
    conditions: list[OpportunityCondition]
    reason_codes: list[str]


class OpportunityTransition(StrictModel):
    transition_id: str
    session: str
    economic_episode_id: str | None
    from_state: OpportunityStateName | None
    to_state: OpportunityStateName
    event_type: str
    reason_codes: list[str]
    evidence_refs: list[str] = Field(default_factory=list)


class OpportunityDay(StrictModel):
    session: str
    state: OpportunityStateName | None
    capability_status: CapabilityStatus
    economic_episode_id: str | None
    opportunity_id: str | None
    setup_date: str | None
    touch_date: str | None
    confirmation_deadline: str | None
    confirmation_date: str | None
    entry_start: str | None
    entry_end: str | None
    eligible: bool | None
    support_zone_id: str | None
    support_test_id: str | None
    conditions: list[OpportunityCondition]
    reason_codes: list[str]


class OpportunityEpisode(StrictModel):
    economic_episode_id: str
    opportunity_id: str
    family: Literal["HEALTHY_PULLBACK"] = "HEALTHY_PULLBACK"
    setup_date: str
    touch_date: str
    confirmation_deadline: str
    confirmation_date: str | None = None
    entry_start: str | None = None
    entry_end: str | None = None
    terminal_date: str | None = None
    state: OpportunityStateName
    zone_id: str
    test_id: str
    zone_lower: float
    zone_upper: float
    anchor_atr: float
    invalidation_line: float
    zone_available_at: str
    recent_high: float
    recent_high_session: str
    parent_episode_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class OpportunityStateCheckpoint(StrictModel):
    symbol: str
    episodes: list[OpportunityEpisode]
    evaluated_through: str | None
    state_revision: int
    committed_result_id: str | None = None
    input_prefix_sha256: str
    source_identity: str
    support_identity: str
    policy_sha256: str
    indicator_identity: str
    price_basis: str
    corporate_action_version: str


class OpportunityInput(StrictModel):
    call_context: CallContext
    feature_view: OpportunityFeatureView
    support_facts: list[OpportunitySupportFact]
    support_result_ids: dict[str, str] = Field(default_factory=dict)
    effective_policy: OpportunityPolicy = Field(default_factory=OpportunityPolicy)
    prior_state: OpportunityStateCheckpoint | None = None
    calendar: str = "XNYS"
    legacy_opinion: dict = Field(default_factory=dict)


class OpportunityCoverage(StrictModel):
    expected_sessions: list[str]
    actual_sessions: list[str]
    missing_sessions: list[str]
    analysis_start: str
    evaluated_through: str | None
    indicator_seed_start: str
    indicator_identity: str
    legal_input_sha256: str
    source_identity: str
    support_identity: str
    fields: dict[str, CapabilityStatus]
    reason_codes: list[str]


class EntryOpportunity(StrictModel):
    module: str = "entry_opportunity"
    version: Literal["1.0"] = "1.0"
    symbol: str
    as_of: str
    status: CapabilityStatus
    state: OpportunityStateName | None
    last_known_state: OpportunityStateName | None
    evaluated_through: str | None
    last_known_session: str | None
    entry_permitted_from: str | None
    entry_permitted_until: str | None
    confirmation_deadline_elapsed_at_requested_session: bool | None
    entry_window_elapsed_at_requested_session: bool | None
    eligible_at_requested_time: bool | None
    economic_episode_id: str | None
    opportunity_id: str | None
    result_id: str
    matched_families: list[str]
    upstream_result_ids: list[str]
    calculation_version: Literal["entry-opportunity-v2"] = "entry-opportunity-v2"
    run_id: str
    request_id: str
    received_at: str | None
    effective_policy: OpportunityPolicy
    policy_sha256: str
    call_context: CallContext
    episodes: list[OpportunityEpisode]
    timeline: list[OpportunityDay]
    transitions: list[OpportunityTransition]
    detections: list[OpportunityDetection]
    current_conditions: list[OpportunityCondition]
    supporting_evidence: list[str]
    opposing_evidence: list[str]
    missing_evidence: list[str]
    next_observation_conditions: list[str]
    legacy_opinion: dict
    opinion_differences: list[dict]
    coverage: OpportunityCoverage
    next_state: OpportunityStateCheckpoint
    call_diagnostics: list[str] = Field(default_factory=list)
    provenance: list[SourceReference]
    explanation: str
