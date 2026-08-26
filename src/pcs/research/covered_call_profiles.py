"""Ticker profiles for the shared covered-call decision layer.

Profiles are explicit research outputs.  Unknown tickers fail closed and
never inherit NVDA parameters.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProfileStatus(StrEnum):
    VALIDATED = "VALIDATED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    NOT_VALIDATED = "NOT_VALIDATED"


@dataclass(frozen=True)
class CoveredCallProfile:
    symbol: str
    status: ProfileStatus
    min_moneyness: float | None = None
    min_atr_distance: float | None = None
    preferred_dte: int | None = None
    preferred_delta: float | None = None
    earnings_applicable: bool = True
    reason_codes: tuple[str, ...] = ()


_NVDA = CoveredCallProfile(
    symbol="NVDA", status=ProfileStatus.VALIDATED, min_moneyness=.20,
    min_atr_distance=3.0, preferred_dte=43, preferred_delta=.20,
    reason_codes=("NVDA_V1_REFERENCE_PROFILE", "RESEARCH_PARAMETERS_FROZEN"))

_NVDL = CoveredCallProfile(
    symbol="NVDL", status=ProfileStatus.NOT_VALIDATED,
    earnings_applicable=True,
    reason_codes=("NVDL_INDEPENDENT_VALIDATION_REQUIRED", "NO_NVDA_PARAMETER_INHERITANCE"))


def resolve_covered_call_profile(symbol: str) -> CoveredCallProfile:
    """Resolve an explicit profile; no fallback to another ticker."""
    ticker = str(symbol).strip().upper()
    if ticker == "NVDA":
        return _NVDA
    if ticker == "NVDL":
        return _NVDL
    return CoveredCallProfile(symbol=ticker, status=ProfileStatus.NOT_VALIDATED,
                              reason_codes=("PROFILE_NOT_VALIDATED", "FAIL_CLOSED"))
