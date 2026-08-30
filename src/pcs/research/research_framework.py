"""Repository-enforced research population routing and funnel contracts.

This module is research-only.  It validates intent before any data runner is
allowed to execute; it does not select contracts or alter production logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping
import json
import re
import yaml


class ResearchMode(StrEnum):
    EXISTING_TRADE = "EXISTING_TRADE"
    NEW_ENTRY = "NEW_ENTRY"
    CONTRACT_VARIANT = "CONTRACT_VARIANT"
    CURRENT_STRATEGY_REPLAY = "CURRENT_STRATEGY_REPLAY"


class ResearchStatus(StrEnum):
    SPEC_INCOMPLETE = "SPEC_INCOMPLETE"
    DAILY_DATA_MISSING = "DAILY_DATA_MISSING"
    OPTIONS_DATA_MISSING = "OPTIONS_DATA_MISSING"
    PIT_FEATURE_MISSING = "PIT_FEATURE_MISSING"
    NO_PRECURSOR_EPISODES = "NO_PRECURSOR_EPISODES"
    NO_SIGNAL_DATES = "NO_SIGNAL_DATES"
    ALL_REMOVED_BY_EVENT_GATE = "ALL_REMOVED_BY_EVENT_GATE"
    CONTRACT_SELECTION_FAILED = "CONTRACT_SELECTION_FAILED"
    ALL_REMOVED_BY_LIQUIDITY_GATE = "ALL_REMOVED_BY_LIQUIDITY_GATE"
    SAMPLE_TOO_SMALL = "SAMPLE_TOO_SMALL"
    COMPUTABLE = "COMPUTABLE"
    COMPLETED = "COMPLETED"


class FunnelStage(StrEnum):
    ALL_TRADING_DAYS = "ALL_TRADING_DAYS"
    FEATURE_READY_DAYS = "FEATURE_READY_DAYS"
    PRECURSOR_EPISODES = "PRECURSOR_EPISODES"
    SIGNAL_DATES = "SIGNAL_DATES"
    EVENT_ELIGIBLE_DATES = "EVENT_ELIGIBLE_DATES"
    CONTRACT_AVAILABLE_DATES = "CONTRACT_AVAILABLE_DATES"
    LIQUIDITY_ELIGIBLE_DATES = "LIQUIDITY_ELIGIBLE_DATES"
    SIMULATED_ENTRIES = "SIMULATED_ENTRIES"


class OnboardingStage(StrEnum):
    DATA_DISCOVERY = "DATA_DISCOVERY"
    DAILY_VALIDATION = "DAILY_VALIDATION"
    OPTIONS_VALIDATION = "OPTIONS_VALIDATION"
    PIT_FEATURE_BUILD = "PIT_FEATURE_BUILD"
    STATE_TIMELINE_BUILD = "STATE_TIMELINE_BUILD"
    CONTRACT_SELECTION_SMOKE_TEST = "CONTRACT_SELECTION_SMOKE_TEST"
    LIFECYCLE_SMOKE_TEST = "LIFECYCLE_SMOKE_TEST"
    RESEARCH_READY = "RESEARCH_READY"


REQUIRED_FIELDS = (
    "research_id", "ticker", "research_mode", "hypothesis", "population_source",
    "signal_definition", "entry_date_rule", "date_range", "split_policy",
    "contract_selection_policy", "lifecycle_policy", "frozen_parameters",
    "allowed_parameters",
)

REQUIRED_NONEMPTY_FIELDS = ("research_mode", "signal_definition", "entry_date_rule")


class ResearchSpecError(ValueError):
    def __init__(self, reason: str, *, status: ResearchStatus = ResearchStatus.SPEC_INCOMPLETE):
        super().__init__(reason)
        self.status = status
        self.reason = reason


PARAMETER_FAMILY_KEYS = {
    "OTM": {"otm", "target_otm", "moneyness", "strike_distance"},
    "DTE": {"dte", "target_dte", "dte_min", "dte_max"},
    "DELTA": {"delta", "target_delta"},
    "ATR": {"atr", "target_atr", "atr_distance"},
    "WIDTH": {"width", "spread_width"},
    "CREDIT": {"credit", "debit", "premium"},
    "LIQUIDITY": {"liquidity", "volume", "open_interest"},
}


def validate_parameter_experiment(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    """Fail closed when an explicitly declared experiment varies >1 family."""
    experiment = spec.get("parameter_experiment")
    if not experiment:
        return spec
    if not isinstance(experiment, Mapping):
        raise ResearchSpecError("PARAMETER_EXPERIMENT_INVALID")
    declared = str(experiment.get("parameter_family", "")).upper()
    candidates = experiment.get("candidates", {})
    if not declared or not isinstance(candidates, Mapping):
        raise ResearchSpecError("PARAMETER_EXPERIMENT_FAMILY_AND_CANDIDATES_REQUIRED")
    varied = {family for family, keys in PARAMETER_FAMILY_KEYS.items()
              if any(key in candidates for key in keys)}
    if varied != {declared}:
        raise ResearchSpecError("PARAMETER_EXPERIMENT_MULTIPLE_INDEPENDENT_FAMILIES:" + ",".join(sorted(varied)))
    return spec


@dataclass(frozen=True)
class ResearchSpec:
    research_id: str
    ticker: str
    research_mode: ResearchMode
    hypothesis: str
    population_source: Mapping[str, Any]
    signal_definition: Mapping[str, Any]
    entry_date_rule: Mapping[str, Any]
    date_range: Mapping[str, Any]
    split_policy: Mapping[str, Any]
    contract_selection_policy: Mapping[str, Any]
    lifecycle_policy: Mapping[str, Any]
    frozen_parameters: Mapping[str, Any]
    allowed_parameters: Mapping[str, Any]
    final_oos_access: bool = False
    production_changes_allowed: bool = False
    rules: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FunnelRecord:
    stage: FunnelStage
    input_count: int
    output_count: int
    affected_count: int
    first_zero_stage: str | None
    status: ResearchStatus
    exact_reason: str
    remediation: str


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchSpecError(f"{name} must be a mapping")
    return dict(value)


def from_mapping(raw: Mapping[str, Any]) -> ResearchSpec:
    missing = [x for x in REQUIRED_FIELDS if x not in raw]
    if missing:
        raise ResearchSpecError("MISSING_REQUIRED_FIELDS:" + ",".join(missing))
    empty = [x for x in REQUIRED_NONEMPTY_FIELDS if not raw.get(x)]
    if empty:
        raise ResearchSpecError("MISSING_NONEMPTY_FIELDS:" + ",".join(empty))
    try:
        mode = ResearchMode(str(raw["research_mode"]).upper())
    except (ValueError, TypeError) as exc:
        raise ResearchSpecError(f"UNKNOWN_RESEARCH_MODE:{raw.get('research_mode')}") from exc
    signal = _mapping(raw["signal_definition"], "signal_definition")
    if not signal:
        raise ResearchSpecError("SIGNAL_DEFINITION_REQUIRED")
    return ResearchSpec(
        research_id=str(raw["research_id"]), ticker=str(raw["ticker"]).upper(),
        research_mode=mode, hypothesis=str(raw["hypothesis"]),
        population_source=_mapping(raw["population_source"], "population_source"),
        signal_definition=signal, entry_date_rule=_mapping(raw["entry_date_rule"], "entry_date_rule"),
        date_range=_mapping(raw["date_range"], "date_range"), split_policy=_mapping(raw["split_policy"], "split_policy"),
        contract_selection_policy=_mapping(raw["contract_selection_policy"], "contract_selection_policy"),
        lifecycle_policy=_mapping(raw["lifecycle_policy"], "lifecycle_policy"),
        frozen_parameters=_mapping(raw["frozen_parameters"], "frozen_parameters"),
        allowed_parameters=_mapping(raw["allowed_parameters"], "allowed_parameters"),
        final_oos_access=bool(raw.get("final_oos_access", False)),
        production_changes_allowed=bool(raw.get("production_changes_allowed", False)),
        rules=_mapping(raw.get("rules", {}), "rules"),
    )


CURRENT_RULE_DEFAULTS = {
    "dte_min": 30, "dte_max": 45, "safe_strike_atr": 2.3,
    "allowed_widths": (5.0, 10.0, 2.0), "width_mode": "ALL",
    "min_credit_width_ratio": 0.10,
    "trend_gate": True, "pullback_gate": True, "support_gate": True,
    "regime_gate": True, "event_gate": True, "liquidity_gate": True,
    "predictability_gate": True,
}


def validate_rule_set(spec: ResearchSpec) -> ResearchSpec:
    """Validate research-local rule data without changing production rules."""
    if spec.research_mode != ResearchMode.CURRENT_STRATEGY_REPLAY:
        return spec
    values = dict(CURRENT_RULE_DEFAULTS)
    values.update(spec.rules)
    unknown = sorted(set(spec.rules) - set(CURRENT_RULE_DEFAULTS))
    if unknown:
        raise ResearchSpecError("UNKNOWN_RULES:" + ",".join(unknown))
    if int(values["dte_min"]) > int(values["dte_max"]):
        raise ResearchSpecError("INVALID_RULES:DTE_MIN_GT_DTE_MAX")
    if float(values["safe_strike_atr"]) < 0 or float(values["min_credit_width_ratio"]) < 0:
        raise ResearchSpecError("INVALID_RULES:NEGATIVE_THRESHOLD")
    widths = tuple(float(x) for x in values["allowed_widths"])
    if not widths or any(x <= 0 for x in widths):
        raise ResearchSpecError("INVALID_RULES:ALLOWED_WIDTHS")
    if str(values["width_mode"]).upper() not in {"ALL", "ONLY"}:
        raise ResearchSpecError("INVALID_RULES:WIDTH_MODE")
    if str(values["width_mode"]).upper() == "ONLY" and len(widths) != 1:
        raise ResearchSpecError("INVALID_RULES:ONLY_REQUIRES_ONE_WIDTH")
    if any(not isinstance(values[k], bool) for k in CURRENT_RULE_DEFAULTS if k.endswith("_gate")):
        raise ResearchSpecError("INVALID_RULES:GATE_MUST_BE_BOOLEAN")
    return spec


def load_spec(path: str | Path) -> ResearchSpec:
    with Path(path).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, Mapping):
        raise ResearchSpecError("SPEC_ROOT_MUST_BE_MAPPING")
    return from_mapping(raw)


def validate_population_routing(spec: ResearchSpec) -> ResearchSpec:
    source = " ".join(str(v).lower() for v in spec.population_source.values())
    frozen = bool(spec.population_source.get("frozen", False)) or bool(
        re.search(r"frozen|candidate.?ledger|trade.?ledger|entry.?ledger", source)
    )
    creates = bool(spec.signal_definition.get("creates_new_entry_dates", False)) or bool(
        spec.signal_definition.get("entry_generation", False)
    )
    if spec.research_mode == ResearchMode.NEW_ENTRY and frozen:
        raise ResearchSpecError("NEW_ENTRY_FORBIDS_FROZEN_LEDGER_POPULATION")
    if spec.research_mode == ResearchMode.NEW_ENTRY and spec.population_source.get("type") != "ticker_daily_calendar":
        raise ResearchSpecError("NEW_ENTRY_REQUIRES_TICKER_DAILY_CALENDAR")
    if spec.research_mode == ResearchMode.EXISTING_TRADE and creates:
        raise ResearchSpecError("EXISTING_TRADE_FORBIDS_NEW_ENTRY_GENERATION")
    if spec.research_mode == ResearchMode.CONTRACT_VARIANT and not bool(
        spec.population_source.get("entry_dates_frozen", False)
    ):
        raise ResearchSpecError("CONTRACT_VARIANT_REQUIRES_FROZEN_ENTRY_DATES")
    if spec.research_mode == ResearchMode.CURRENT_STRATEGY_REPLAY and spec.population_source.get("type") != "ticker_daily_calendar":
        raise ResearchSpecError("CURRENT_STRATEGY_REPLAY_REQUIRES_TICKER_DAILY_CALENDAR")
    return spec


def spec_hash(spec: ResearchSpec) -> str:
    import hashlib
    payload = asdict(spec)
    payload["research_mode"] = str(spec.research_mode)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def build_funnel(counts: Mapping[str, int], *, reasons: Mapping[str, str] | None = None,
                 remediations: Mapping[str, str] | None = None) -> list[FunnelRecord]:
    stages = list(FunnelStage)
    records: list[FunnelRecord] = []
    first_zero: str | None = None
    for i, stage in enumerate(stages):
        out = int(counts.get(stage.value, 0))
        inp = int(counts.get(stages[i - 1].value, out)) if i else out
        if first_zero is None and out == 0:
            first_zero = stage.value
        records.append(FunnelRecord(stage, inp, out, max(inp - out, 0), first_zero,
            ResearchStatus.COMPLETED if first_zero is None else _zero_status(stage),
            (reasons or {}).get(stage.value, "stage has observations" if out else f"no rows at {stage.value}"),
            (remediations or {}).get(stage.value, "provide the missing upstream input or verify the stage definition" if out == 0 else "none")))
    return records


def _zero_status(stage: FunnelStage) -> ResearchStatus:
    return {
        FunnelStage.ALL_TRADING_DAYS: ResearchStatus.DAILY_DATA_MISSING,
        FunnelStage.FEATURE_READY_DAYS: ResearchStatus.PIT_FEATURE_MISSING,
        FunnelStage.PRECURSOR_EPISODES: ResearchStatus.NO_PRECURSOR_EPISODES,
        FunnelStage.SIGNAL_DATES: ResearchStatus.NO_SIGNAL_DATES,
        FunnelStage.EVENT_ELIGIBLE_DATES: ResearchStatus.ALL_REMOVED_BY_EVENT_GATE,
        FunnelStage.CONTRACT_AVAILABLE_DATES: ResearchStatus.CONTRACT_SELECTION_FAILED,
        FunnelStage.LIQUIDITY_ELIGIBLE_DATES: ResearchStatus.ALL_REMOVED_BY_LIQUIDITY_GATE,
        FunnelStage.SIMULATED_ENTRIES: ResearchStatus.SAMPLE_TOO_SMALL,
    }[stage]


def onboarding_report(stage_status: Mapping[str, str]) -> dict[str, Any]:
    stages = list(OnboardingStage)
    first_failed = next((s.value for s in stages if stage_status.get(s.value) not in (None, "PASS", "RESEARCH_READY")), None)
    return {"stages": [{"stage": s.value, "status": stage_status.get(s.value, "NOT_RUN")} for s in stages],
            "first_blocking_stage": first_failed, "research_ready": first_failed is None and stage_status.get("RESEARCH_READY") == "PASS"}


def assert_research_output(path: str | Path) -> None:
    text = str(Path(path)).replace("\\", "/").lower()
    if "/production/" in text or "frozen_artifact" in text or "/frozen/" in text:
        raise PermissionError("RESEARCH_RUNNER_CANNOT_WRITE_PRODUCTION_OR_FROZEN_ARTIFACT")


def assert_final_oos_access(spec: ResearchSpec) -> None:
    if not spec.final_oos_access:
        raise PermissionError("FINAL_OOS_ACCESS_NOT_AUTHORIZED_BY_RESEARCH_SPEC")


def run_spec(path: str | Path, *, dry_run: bool = True) -> dict[str, Any]:
    spec = validate_population_routing(load_spec(path))
    return {"research_id": spec.research_id, "ticker": spec.ticker, "research_mode": spec.research_mode.value,
            "population_source": dict(spec.population_source), "entry_date_rule": dict(spec.entry_date_rule),
            "scenario_hash": spec_hash(spec), "final_oos_access": spec.final_oos_access,
            "production_changes_allowed": spec.production_changes_allowed, "execution": "DRY_RUN_ONLY" if dry_run else "VALIDATED_RUN",
            "status": ResearchStatus.COMPUTABLE.value, "hypothesis_executed": False,
            "reason_codes": ["RESEARCH_SPEC_VALIDATED", "FINAL_OOS_NOT_READ", "PRODUCTION_WRITE_BLOCKED"]}
