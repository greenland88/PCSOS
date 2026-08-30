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
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class CoveredCallProfile:
    symbol: str
    status: ProfileStatus
    min_moneyness: float | None = None
    min_atr_distance: float | None = None
    preferred_dte: int | None = None
    preferred_delta: float | None = None
    max_calls: int = 3
    earnings_applicable: bool = True
    reason_codes: tuple[str, ...] = ()
    sell_timing: dict[str, object] | None = None
    contract_selection: dict[str, object] | None = None
    liquidity: dict[str, object] | None = None
    profit_management: dict[str, object] | None = None
    roll: dict[str, object] | None = None
    events: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable frozen-profile envelope."""
        from dataclasses import asdict
        result = asdict(self)
        result["status"] = self.status.value
        return result


_NVDA = CoveredCallProfile(
    symbol="NVDA", status=ProfileStatus.VALIDATED, min_moneyness=.125,
    min_atr_distance=None, preferred_dte=30, preferred_delta=None,
    max_calls=1, reason_codes=("NVDA_POST_SPLIT_PROFILE", "PARAMETERS_FROZEN"),
    sell_timing={"policy": "ALWAYS_SELL"},
    contract_selection={"method": "NEAREST_OTM_DTE", "target_otm": .125,
                        "target_dte": 30, "price_basis": "MARKET_RAW"},
    liquidity={"exact_bid_ask": True}, profit_management={"status": "NOT_ENABLED"},
    roll={"status": "NOT_ENABLED"}, events={"assignment": "DISALLOWED"})

_NVDL = CoveredCallProfile(
    symbol="NVDL", status=ProfileStatus.NOT_VALIDATED,
    earnings_applicable=True,
    reason_codes=("NVDL_INDEPENDENT_VALIDATION_REQUIRED", "NO_NVDA_PARAMETER_INHERITANCE"))

_RESEARCH_ONLY = {
    ticker: CoveredCallProfile(
        symbol=ticker, status=ProfileStatus.RESEARCH_ONLY,
        reason_codes=(f"{ticker}_INDEPENDENT_RESEARCH_REQUIRED", "NO_CROSS_TICKER_PARAMETER_INHERITANCE"),
        sell_timing={"status": "RESEARCH_REQUIRED"},
        contract_selection={"status": "RESEARCH_REQUIRED"},
        liquidity={"exact_bid_ask": True}, profit_management={"status": "RESEARCH_REQUIRED"},
        roll={"max_roll_count": 10}, events={"earnings": "RESEARCH_REQUIRED"})
    for ticker in ("QQQ", "SPY", "AMD")
}


def resolve_covered_call_profile(symbol: str) -> CoveredCallProfile:
    """Resolve an explicit profile; no fallback to another ticker."""
    ticker = str(symbol).strip().upper()
    if ticker == "NVDA":
        return _NVDA
    if ticker == "NVDL":
        return _NVDL
    if ticker in _RESEARCH_ONLY:
        return _RESEARCH_ONLY[ticker]
    return CoveredCallProfile(symbol=ticker, status=ProfileStatus.NOT_VALIDATED,
                              reason_codes=("PROFILE_NOT_VALIDATED", "FAIL_CLOSED"))
