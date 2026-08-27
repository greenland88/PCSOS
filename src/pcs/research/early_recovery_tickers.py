"""Research-only ticker registry for DAILY_EARLY_RECOVERY_CONFIRMATION."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EarlyRecoveryTickerSpec:
    ticker: str
    candidate_source: str
    lifecycle_source: str | None
    state_source: str | None
    split_source: str
    option_dataset: str
    option_root: str
    coverage_note: str


REGISTRY = {
    "SPY": EarlyRecoveryTickerSpec(
        "SPY", "research_outputs/spy_qqq_underlying_state_research_20260821/policy_entries.parquet",
        "research_outputs/spy_qqq_underlying_state_research_20260821/policy_entries.parquet",
        "research_outputs/spy_qqq_underlying_state_research_20260821/daily_underlying_state_ledger.parquet",
        "research_outputs/spy_qqq_pcs_baseline_20260821/split_manifest.json", "options_v3", "data/parquet/options_v3/symbol=SPY",
        "SPY/QQQ preserved available-context baseline; state ledger already persisted"),
    "QQQ": EarlyRecoveryTickerSpec(
        "QQQ", "research_outputs/spy_qqq_underlying_state_research_20260821/policy_entries.parquet",
        "research_outputs/spy_qqq_underlying_state_research_20260821/policy_entries.parquet",
        "research_outputs/spy_qqq_underlying_state_research_20260821/daily_underlying_state_ledger.parquet",
        "research_outputs/spy_qqq_pcs_baseline_20260821/split_manifest.json", "options_v3", "data/parquet/options_v3/symbol=QQQ",
        "SPY/QQQ preserved available-context baseline; state ledger already persisted"),
    "AMD": EarlyRecoveryTickerSpec(
        "AMD", "research_outputs/phase0_20260820/candidate_universe.parquet",
        "research_outputs/phase0_20260820/lifecycle_marks.parquet", None,
        "UNIFIED_DATE_BOUNDARY_2020-02-28_2026-05-31", "options_v2", "data/parquet/options_v2/symbol=AMD",
        "474 authoritative frozen candidates; 49 entry dates are after the research/validation boundary"),
}


def get_early_recovery_ticker(ticker: str) -> EarlyRecoveryTickerSpec:
    key = str(ticker).upper()
    try:
        return REGISTRY[key]
    except KeyError as exc:
        raise ValueError(f"unsupported research ticker: {ticker}; registered={sorted(REGISTRY)}") from exc


def registry_paths(root: Path, ticker: str) -> dict[str, Path | None]:
    spec = get_early_recovery_ticker(ticker)
    return {
        "candidate_source": root / spec.candidate_source,
        "lifecycle_source": root / spec.lifecycle_source if spec.lifecycle_source else None,
        "state_source": root / spec.state_source if spec.state_source else None,
    }
