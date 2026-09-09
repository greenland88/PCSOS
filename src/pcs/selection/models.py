"""Step 8 transport-neutral observation contracts."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
import re
from pydantic import Field, field_validator, model_validator
from pcs.analysis_contracts import StrictModel, SourceReference
from pcs.trend.selection_models import EntryOpportunity, OpportunityCondition, SupportZoneResult

Family = Literal['HEALTHY_PULLBACK','SHALLOW_PULLBACK','BREAKOUT_RETEST','CONSTRUCTIVE_BASE']
FAMILIES = ['HEALTHY_PULLBACK','SHALLOW_PULLBACK','BREAKOUT_RETEST','CONSTRUCTIVE_BASE']
Group = Literal['READY_FOR_OPTIONS_REVIEW','WATCH_SETUP','NOT_CURRENTLY_APPLICABLE','NO_SETUP','INSUFFICIENT_EVIDENCE']
GROUPS = ['READY_FOR_OPTIONS_REVIEW','WATCH_SETUP','NOT_CURRENTLY_APPLICABLE','NO_SETUP','INSUFFICIENT_EVIDENCE']
KEYS = ['current_required_complete','live_support_class','confirmation_fraction','remaining_entry_sessions','dollar_volume_median_20','symbol']


class BatchContext(StrictModel):
    requested_as_of: str
    requested_session: str
    effective_daily_session: str
    mode: Literal['HISTORICAL','CURRENT_EOD'] = 'HISTORICAL'
    calendar: str = 'XNYS'
    run_id: str = 'stock-observation'
    request_id: str = 'stock-observation'

    @model_validator(mode='after')
    def dates(self):
        date.fromisoformat(self.requested_session)
        date.fromisoformat(self.effective_daily_session)
        if self.mode == 'HISTORICAL' and self.requested_as_of != self.requested_session:
            raise ValueError('HISTORICAL_REQUEST_SESSION_MISMATCH')
        if self.mode == 'CURRENT_EOD' and datetime.fromisoformat(self.requested_as_of).tzinfo is None:
            raise ValueError('REQUEST_TIMEZONE_REQUIRED')
        return self


class SortField(StrictModel):
    field: str
    direction: Literal['ASC','DESC']
    nulls: Literal['LAST'] = 'LAST'


class RankingPolicy(StrictModel):
    policy_id: str = 'stock-observation-ranking-v1'
    calculation_version: Literal['stock-observation-ranking-v1'] = 'stock-observation-ranking-v1'
    group_order: list[Group] = Field(default_factory=lambda: list(GROUPS))
    sort_fields: list[SortField] = Field(default_factory=lambda: [SortField(field=k,direction='ASC' if k in {'live_support_class','symbol'} else 'DESC') for k in KEYS])
    live_test_order: list[str] = Field(default_factory=lambda: ['MULTIPLE_INDEPENDENT_HELD','SINGLE_HELD','IN_PROGRESS','UNCONFIRMED_TEST','UNKNOWN'])
    representative_order: list[str] = Field(default_factory=lambda: ['CURRENT_APPLICABILITY','CURRENT_REQUIRED_COMPLETE','CONFIRMATION_DATE_DESC_NULL_LAST','STABLE_ID_ASC'])
    liquidity_currency: str = 'USD'
    purpose: Literal['OBSERVATION_ONLY_NOT_RETURN_VALIDATED'] = 'OBSERVATION_ONLY_NOT_RETURN_VALIDATED'

    @model_validator(mode='after')
    def complete_policy(self):
        if sorted(self.group_order) != sorted(GROUPS) or sorted(f.field for f in self.sort_fields) != sorted(KEYS):
            raise ValueError('RANKING_POLICY_FIELDS_INVALID')
        if self.live_test_order != ['MULTIPLE_INDEPENDENT_HELD','SINGLE_HELD','IN_PROGRESS','UNCONFIRMED_TEST','UNKNOWN']:
            raise ValueError('LIVE_TEST_POLICY_ORDER_INVALID')
        if self.representative_order != ['CURRENT_APPLICABILITY','CURRENT_REQUIRED_COMPLETE','CONFIRMATION_DATE_DESC_NULL_LAST','STABLE_ID_ASC']:
            raise ValueError('REPRESENTATIVE_POLICY_UNSUPPORTED')
        return self


class ResultBinding(StrictModel):
    result_id: str
    symbol: str
    family: Family
    source_bundle: str
    manifest_sha256: str
    content_sha256: str
    schema_version: str
    calculation_version: str
    feature_identity: str | None = None
    price_basis: str
    indicator_identity: str
    corporate_action_version: str
    calendar: str
    source_identity: str


class InputFailure(StrictModel):
    symbol: str
    family: Family | None = None
    stage: str
    reason_codes: list[str]
    source_refs: list[str] = Field(default_factory=list)


class SharedFact(StrictModel):
    symbol: str
    identity: str
    known_at: str
    value: Any
    source_refs: list[str]


class Diagnostic(StrictModel):
    symbol: str
    metric_id: str
    result_id: str
    as_of: str
    value: float | None
    unit: str | None
    currency: str | None
    price_basis: str | None
    validated: bool
    source_refs: list[str]
    record: dict[str, Any] = Field(default_factory=dict)


class LegacyAssessment(StrictModel):
    symbol: str
    as_of: str
    result_id: str
    timing_eligible: bool | None = None
    trend: dict = Field(default_factory=dict)
    timing: dict = Field(default_factory=dict)
    options: dict = Field(default_factory=dict)
    action: str | None = None
    source_refs: list[str] = Field(default_factory=list)


class ContextFact(StrictModel):
    entity: str
    claim: str
    known_at: str
    source: SourceReference
    value: Any = None


class CompanyContext(StrictModel):
    entity: str | None = None
    industry: str | None = None
    industry_position_claims: list[ContextFact] = Field(default_factory=list)
    fundamental_evidence: list[ContextFact] = Field(default_factory=list)
    status: Literal['NOT_EVALUATED','PROVIDED'] = 'NOT_EVALUATED'


class OptionalContext(StrictModel):
    status: Literal['NOT_EVALUATED','PROVIDED'] = 'NOT_EVALUATED'
    facts: list[ContextFact] = Field(default_factory=list)


class OptionsContext(StrictModel):
    data_status: str = 'NOT_PROVIDED'
    quote_as_of: str | None = None
    discovery_status: str = 'NOT_EVALUATED'
    formal_evaluation_status: str = 'NOT_EVALUATED'
    source_refs: list[str] = Field(default_factory=list)
    future_preference_dte: tuple[int,int] = (25,45)
    preference_scope: Literal['USER_PREFERENCE_NOT_PRODUCTION_POLICY'] = 'USER_PREFERENCE_NOT_PRODUCTION_POLICY'


class RankingInput(StrictModel):
    context: BatchContext
    requested_symbols: list[str]
    enabled_families: list[Family]
    opportunities: list[EntryOpportunity]
    bindings: list[ResultBinding] = Field(default_factory=list)
    selected_result_ids: dict[str,str] = Field(default_factory=dict)  # symbol|session|family -> result_id
    failures: list[InputFailure] = Field(default_factory=list)
    shared_facts: list[SharedFact] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    legacy: list[LegacyAssessment] = Field(default_factory=list)
    supports: list[SupportZoneResult] = Field(default_factory=list)
    policy: RankingPolicy = Field(default_factory=RankingPolicy)
    previous: StockShortlist | None = None
    adapter_version: Literal['saved-selection-adapter-v1'] = 'saved-selection-adapter-v1'

    @field_validator('requested_symbols')
    @classmethod
    def symbols(cls, values):
        result = sorted(set(v.strip().upper() for v in values))
        if not result or any(not re.fullmatch(r'[A-Z][A-Z0-9._-]*',v) for v in result):
            raise ValueError('EXPLICIT_SYMBOL_SCOPE_REQUIRED')
        return result


class SortKey(StrictModel):
    field: str
    value: float | str | bool | None
    unit: str | None
    direction: Literal['ASC','DESC']
    available: bool
    source_refs: list[str]
    reason_codes: list[str]
    details: dict[str, Any] = Field(default_factory=dict)


class FamilyAssessment(StrictModel):
    family: Family
    result_id: str | None
    candidate_result_ids: list[str] = Field(default_factory=list)
    opportunity_id: str | None = None
    state: str | None
    data_session: str | None = None
    requested_session: str | None = None
    request_time_semantics: str | None = None
    eligible_at_requested_time: bool | None
    execution: Literal['EXECUTED','NOT_EXECUTED','NOT_ENABLED']
    applicability: Group
    current_required_complete: bool
    confirmation_date: str | None = None
    setup_date: str | None = None
    touch_date: str | None = None
    confirmation_deadline: str | None = None
    entry_start: str | None = None
    entry_end: str | None = None
    support_zone_id: str | None = None
    reason_codes: list[str]
    current_gaps: list[dict] = Field(default_factory=list)
    coverage_gaps: list[dict] = Field(default_factory=list)
    conditions: list[OpportunityCondition] = Field(default_factory=list)
    representative_key: list[Any] = Field(default_factory=list)


class StockRow(StrictModel):
    row_id: str
    symbol: str
    group: Group
    rank: int = 0
    group_rank: int = 0
    representative_result_id: str | None
    representative_opportunity_id: str | None
    representative_candidates: list[dict]
    family_assessments: list[FamilyAssessment]
    active_families: list[str]
    watch_families: list[str]
    historical_families: list[str]
    current_eligible: bool | None
    current_required_complete: bool
    coverage_complete: bool
    sort_keys: list[SortKey]
    supporting_evidence: list[dict]
    opposing_evidence: list[dict]
    current_gaps: list[dict]
    coverage_gaps: list[dict]
    next_observation_conditions: list[str]
    invalidation_conditions: list[dict]
    options_context: OptionsContext = Field(default_factory=OptionsContext)
    company_context: CompanyContext = Field(default_factory=CompanyContext)
    old_new_comparison: dict
    packet_id: str | None = None
    reason_codes: list[str]


class StockShortlist(StrictModel):
    module: Literal['stock_shortlist'] = 'stock_shortlist'
    version: Literal['1.0'] = '1.0'
    calculation_version: Literal['stock-observation-ranking-v1'] = 'stock-observation-ranking-v1'
    shortlist_id: str
    as_of: str
    status: Literal['COMPLETED'] = 'COMPLETED'
    data_timestamp: str | None
    run_id: str
    request_id: str
    context: BatchContext
    requested_symbols: list[str]
    enabled_families: list[Family]
    effective_policy: RankingPolicy
    policy_sha256: str
    rows: list[StockRow]
    input_bindings: list[ResultBinding]
    adapter_version: str
    coverage_complete: bool
    changes_status: Literal['COMPARED','NOT_COMPARED']
    changes: list[dict]
    reason_codes: list[str]


class EvidenceRef(StrictModel):
    record_scope: Literal['NORMALIZED_ATTACHMENT'] = 'NORMALIZED_ATTACHMENT'
    evidence_id: str
    source_result_id: str
    entity_id: str
    json_pointer: str
    known_at: str
    schema_version: str
    content_sha256: str
    source_bundle: str


class EvidenceRecord(StrictModel):
    ref: EvidenceRef
    kind: str
    value: dict[str, Any]


class DecisionPacketInput(StrictModel):
    symbol: str
    selection_input: RankingInput
    shortlist: StockShortlist | None = None
    extra_records: list[EvidenceRecord] = Field(default_factory=list)
    company_context: CompanyContext = Field(default_factory=CompanyContext)
    market_context: OptionalContext = Field(default_factory=OptionalContext)
    portfolio_context: OptionalContext = Field(default_factory=OptionalContext)
    options_context: OptionsContext = Field(default_factory=OptionsContext)


class DecisionEvidencePacket(StrictModel):
    module: Literal['decision_evidence_packet'] = 'decision_evidence_packet'
    version: Literal['1.0'] = '1.0'
    calculation_version: Literal['decision-evidence-packet-v1'] = 'decision-evidence-packet-v1'
    packet_id: str
    content_identity: str
    symbol: str
    as_of: str
    status: Literal['COMPLETED','PARTIAL','NOT_IN_INPUT_SCOPE']
    data_timestamp: str | None
    run_id: str
    request_id: str
    context: BatchContext
    evidence_scope: dict
    component_refs: list[dict]
    program_assessments: list[dict]
    supporting_evidence: list[EvidenceRef]
    opposing_evidence: list[EvidenceRef]
    current_gaps: list[dict]
    coverage_gaps: list[dict]
    next_observation_conditions: list[dict]
    invalidation_conditions: list[dict]
    alternatives_and_disagreements: list[dict]
    discussion_questions: list[str]
    options_context: OptionsContext
    company_context: CompanyContext
    market_context: OptionalContext
    portfolio_context: OptionalContext
    detail_index: list[EvidenceRef]
    evidence_records: list[EvidenceRecord]
    shortlist_id: str | None = None
    ai_review_status: Literal['NOT_REVIEWED'] = 'NOT_REVIEWED'
    user_decision_status: Literal['NOT_RECORDED'] = 'NOT_RECORDED'
    reason_codes: list[str]


class EvidenceQuery(StrictModel):
    packet: DecisionEvidencePacket
    evidence_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceQueryResult(StrictModel):
    status: Literal['RESOLVED','NOT_FOUND','UNRESOLVED']
    record: EvidenceRecord | None
    records: list[EvidenceRecord] = Field(default_factory=list)
    unresolved_refs: list[str] = Field(default_factory=list)
    reason_codes: list[str]


class ReviewSubmission(StrictModel):
    review_id: str | None = None
    packet_id: str
    packet_content_identity: str
    generated_at: str
    origin: Literal['EXTERNAL_AI','MANUAL','TEST']
    model: str | None = None
    prompt_version: str | None = None
    recommendation: str
    scope: Literal['STOCK_RESEARCH','CONTRACT_HYPOTHESIS']
    supporting_refs: list[str] = Field(default_factory=list)
    opposing_refs: list[str] = Field(default_factory=list)
    external_sources: list[ContextFact] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    disagreements_with_program: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    change_conditions: list[str] = Field(default_factory=list)

    @field_validator('generated_at')
    @classmethod
    def timezone_required(cls, value):
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError('REVIEW_TIMEZONE_REQUIRED')
        return value


class AIReview(ReviewSubmission):
    review_id: str
    content_sha256: str
    status: Literal['VALIDATED_IMPORT'] = 'VALIDATED_IMPORT'
    module: Literal['ai_review'] = 'ai_review'
    version: Literal['1.0'] = '1.0'
    calculation_version: Literal['ai-review-record-v1'] = 'ai-review-record-v1'
    model_called_by_pcs: Literal[False] = False
    user_decision_status: Literal['NOT_RECORDED'] = 'NOT_RECORDED'


class AIReviewInput(StrictModel):
    packet: DecisionEvidencePacket
    submission: ReviewSubmission
    existing_reviews: list[AIReview] = Field(default_factory=list)


RankingInput.model_rebuild()
