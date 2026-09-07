"""Small, transport-neutral contracts shared by PCS analysis components."""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceStatus(StrEnum):
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    CONFIGURED_ASSUMPTION = "CONFIGURED_ASSUMPTION"
    MISSING = "MISSING"
    NOT_RECORDED = "NOT_RECORDED"


class ExecutionStatus(StrEnum):
    EXECUTED = "EXECUTED"
    BLOCKED = "BLOCKED"
    NOT_EVALUATED = "NOT_EVALUATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CapabilityStatus(StrEnum):
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    NOT_EVALUATED = "NOT_EVALUATED"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CallContext(StrictModel):
    symbol: str
    requested_as_of: str
    effective_daily_session: str | None = None
    mode: str
    run_id: str
    request_id: str
    scope: str = "SELECTION_EXPLANATION"

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("symbol is required")
        return value


class SourceReference(StrictModel):
    source_id: str
    source_kind: str
    schema_version: str | None = None
    sha256: str | None = None
    record_identity: str | None = None
    validated: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


class EvidenceValue(StrictModel):
    metric_id: str
    value: Any = None
    unit: str | None = None
    source_status: SourceStatus
    execution_status: ExecutionStatus = ExecutionStatus.EXECUTED
    producer: str
    source_field: str | None = None
    data_time: str | None = None
    definition_ref: str
    window_sessions: int | None = None
    sample_count: int | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    consumed_in_source_run: bool | None = None
    notes: list[str] = Field(default_factory=list)


class RuleEvaluation(StrictModel):
    rule_id: str
    rule_version: str
    rule_role: str
    execution_status: ExecutionStatus
    gate_outcome: str
    predicate_value: bool | None = None
    operator: str | None = None
    threshold: Any = None
    unit: str | None = None
    input_refs: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    policy_ref: str | None = None


class ExplanationInput(StrictModel):
    call_context: CallContext
    ticker_result: dict[str, Any]
    ai_evidence: dict[str, Any] | None = None
    effective_policy: dict[str, Any] = Field(default_factory=dict)
    source_references: list[SourceReference]


class SelectionExplanationData(StrictModel):
    identity: dict[str, Any]
    time_context: dict[str, Any]
    capabilities: dict[str, Any]
    measurements: list[EvidenceValue]
    entities: list[dict[str, Any]]
    rule_evaluations: list[RuleEvaluation]
    assessment: dict[str, Any]
    effective_policy: dict[str, Any]
    data_quality: dict[str, Any]
    next_conditions: list[dict[str, Any]]
    provenance: list[SourceReference]
    detail_index: list[dict[str, Any]]
    selection_explanation: dict[str, Any]
    entrypoint_disagreements: list[dict[str, Any]]
    metadata_unknowns: list[str]
    score_provenance: list[EvidenceValue]


class SelectionExplanation(StrictModel):
    module: str = "selection_explanation"
    version: str = "1.0"
    symbol: str
    as_of: str
    status: str
    data_timestamp: str | None
    calculation_version: str = "selection-explanation-v1.4-step1.1"
    run_id: str
    request_id: str
    reason_codes: list[str]
    data: SelectionExplanationData
    explanation: str
