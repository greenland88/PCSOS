"""Explicit read-only observation contracts; production defaults are separate."""
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import exchange_calendars as xc
import pandas as pd
from pydantic import Field, model_validator
from pcs.analysis_contracts import StrictModel
from pcs.selection.models import BatchContext, RankingPolicy, DecisionEvidencePacket, EvidenceQueryResult, FAMILIES
from pcs.trend.selection_models import (ProfilePolicy, SupportZonePolicy, OpportunityPolicy,
    ShallowPullbackPolicy, BreakoutRetestPolicy, ConstructiveBasePolicy)


class SelectionProfile(StrictModel):
    profile_id: Literal['selection-v2-observation-v1'] = 'selection-v2-observation-v1'
    families: list[str] = Field(default_factory=lambda:list(FAMILIES))
    profile: ProfilePolicy = Field(default_factory=ProfilePolicy)
    support: SupportZonePolicy = Field(default_factory=SupportZonePolicy)
    opportunity: OpportunityPolicy = Field(default_factory=OpportunityPolicy)
    shallow: ShallowPullbackPolicy = Field(default_factory=ShallowPullbackPolicy)
    breakout: BreakoutRetestPolicy = Field(default_factory=BreakoutRetestPolicy)
    base: ConstructiveBasePolicy = Field(default_factory=ConstructiveBasePolicy)
    ranking: RankingPolicy = Field(default_factory=RankingPolicy)

    @model_validator(mode='after')
    def validate_families(self):
        if not self.families or len(set(self.families))!=len(self.families) or set(self.families)-set(FAMILIES):
            raise ValueError('OBSERVATION_FAMILY_SCOPE_INVALID')
        if self.support.calculation_version!='support-zones-v2':
            raise ValueError('OBSERVATION_ACCEPTED_SUPPORT_VERSION_REQUIRED')
        return self


class ObservationBudgets(StrictModel):
    verification_seconds: float = Field(default=120,gt=0,le=3600)
    preparation_seconds: float = Field(default=180,gt=0,le=3600)
    component_seconds: float = Field(default=120,gt=0,le=3600)
    output_seconds: float = Field(default=180,gt=0,le=3600)
    total_seconds: float = Field(default=7200,gt=0,le=86400)


class StockObservationInput(StrictModel):
    version: Literal['1.0'] = '1.0'
    scope: Literal['STOCK_OBSERVATION'] = 'STOCK_OBSERVATION'
    symbols: list[str]
    universe_id: str = 'explicit'
    universe_source: str | None = None
    universe_sha256: str | None = None
    universe_members_field: Literal['symbols','included_symbols'] = 'symbols'
    context: BatchContext
    selection_profile: SelectionProfile = Field(default_factory=SelectionProfile)
    data_mode: Literal['READ_ONLY'] = 'READ_ONLY'
    output_directory: str
    manifest_path: str = 'data/manifests/storage_manifest.csv'
    parquet_root: str = 'data/parquet'
    max_workers: int = Field(default=4,ge=1,le=16)
    budgets: ObservationBudgets = Field(default_factory=ObservationBudgets)
    previous_run: str | None = None
    resume_run_id: str | None = None
    run_id: str = Field(default_factory=lambda:uuid4().hex)
    # Only a hash-verified Step 8 input manifest; no arbitrary "verified" dicts.
    saved_selection_manifest: str | None = None

    @model_validator(mode='after')
    def validate_request(self):
        import re
        values=sorted({s.strip().upper() for s in self.symbols})
        if not values or any(not re.fullmatch(r'[A-Z][A-Z0-9._-]*',s) for s in values):
            raise ValueError('OBSERVATION_SYMBOL_SCOPE_INVALID')
        object.__setattr__(self,'symbols',values)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',self.run_id):
            raise ValueError('OBSERVATION_RUN_ID_INVALID')
        if Path(self.output_directory).resolve()==Path('pool_scan_runs').resolve():
            raise ValueError('OBSERVATION_REQUIRES_SEPARATE_OUTPUT_ROOT')
        cal=xc.get_calendar(self.context.calendar)
        if self.context.mode=='HISTORICAL':
            if not cal.is_session(self.context.requested_session) or self.context.requested_session!=self.context.effective_daily_session:
                raise ValueError('OBSERVATION_HISTORICAL_SESSION_INVALID')
        else:
            from pcs.analysis_contracts import CallContext
            from pcs.trend.opportunity_state import _resolve_requested_session
            actual,_=_resolve_requested_session(CallContext(symbol=values[0],
                requested_as_of=self.context.requested_as_of,
                effective_daily_session=self.context.effective_daily_session,mode='CURRENT_EOD',
                run_id=self.run_id,request_id=self.context.request_id),self.context.calendar)
            if actual!=self.context.effective_daily_session or actual!=self.context.requested_session:
                raise ValueError('OBSERVATION_CURRENT_EOD_CONTEXT_MISMATCH')
        return self


class ObservationResumeInput(StrictModel):
    spec: StockObservationInput
    run_id: str


class ComponentRef(StrictModel):
    file: str
    sha256: str
    kind: str
    symbol: str
    dependency_id: str
    result_id: str | None = None
    revision: int
    computed_at: str


class ObservationSymbol(StrictModel):
    symbol: str
    execution: Literal['COMPLETED','DATA_BLOCKED','FAILED','TIMED_OUT','UNPROCESSED'] = 'UNPROCESSED'
    components: dict[str,ComponentRef] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    served_at: str | None = None
    served_attempt: str | None = None
    input_seed_start: str | None = None
    component_failures: dict[str,list[str]] = Field(default_factory=dict)
    cache_hits: int = 0
    recomputed: int = 0
    lineage: list[dict] = Field(default_factory=list)


class StockObservationRun(StrictModel):
    module: Literal['stock_observation_run'] = 'stock_observation_run'
    version: Literal['1.0'] = '1.0'
    calculation_version: Literal['stock-observation-integration-v1'] = 'stock-observation-integration-v1'
    scope: Literal['STOCK_OBSERVATION'] = 'STOCK_OBSERVATION'
    run_id: str
    attempt_id: str
    status: Literal['COMPLETED','PARTIAL','FAILED']
    coverage: Literal['COMPLETE','PARTIAL']
    context: BatchContext
    source_commit: str
    spec_id: str
    profile_id: str
    universe_id: str
    requested_symbols: list[str]
    output_directory: str
    checkpoint: str
    summary: dict
    selection_v2: dict
    options_status: Literal['NOT_REQUESTED'] = 'NOT_REQUESTED'
    options_calls: Literal[0] = 0
    overlay_status: Literal['NOT_REQUESTED'] = 'NOT_REQUESTED'
    legacy_counts: dict = Field(default_factory=lambda:{key:None for key in (
        'daily_ready','daily_data_blocked','timing_successfully_evaluated','timing_evidence_unavailable',
        'timing_timeout_or_not_started','timing_candidates','options_successfully_evaluated')})
    current_published: bool = False
    reason_codes: list[str] = Field(default_factory=list)


class ObservationQuery(StrictModel):
    run_directory: str
    symbol: str
    evidence_id: str | None = None


class ObservationQueryResult(StrictModel):
    status: Literal['RESOLVED','NOT_IN_INPUT_SCOPE','NOT_EXECUTED']
    symbol: str
    state: ObservationSymbol | None = None
    packet: DecisionEvidencePacket | None = None
    evidence: EvidenceQueryResult | None = None
    reason_codes: list[str] = Field(default_factory=list)
