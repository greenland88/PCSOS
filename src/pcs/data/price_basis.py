"""Canonical price-basis and simple corporate-action conversion service.

Technical features remain in ANALYTIC_ADJUSTED space.  Option economics and
identity remain in MARKET_RAW space; only comparisons cross this boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable
import pandas as pd
from pathlib import Path
import csv
from typing import Any


class PriceBasis(StrEnum):
    MARKET_RAW = "MARKET_RAW"
    ANALYTIC_ADJUSTED = "ANALYTIC_ADJUSTED"
    UNKNOWN = "UNKNOWN"


class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"


class PriceBasisError(ValueError):
    pass


def assert_comparable_contract(*, spot: float, strike: float,
                               price_basis: PriceBasis,
                               sane_upper_bound: float = 2.0) -> None:
    """Fail closed when a spot/strike pair is not economically comparable."""
    if price_basis == PriceBasis.UNKNOWN:
        raise PriceBasisError("PRICE_BASIS_UNKNOWN")
    if float(spot) <= 0 or float(strike) <= 0:
        raise PriceBasisError("PRICE_BASIS_MISMATCH")
    ratio = float(strike) / float(spot)
    if ratio >= float(sane_upper_bound):
        raise PriceBasisError("PRICE_BASIS_MISMATCH")


def transform_frame_to_basis(frame: Any, *, symbol: str, date_column: str,
                             from_basis: PriceBasis, to_basis: PriceBasis,
                             registry: "CorporateActionRegistry") -> Any:
    """Transform absolute price fields while preserving dimensionless features.

    Daily adjusted OHLC, ATR, SMA and price levels are converted to the
    requested market basis. Option premiums are deliberately excluded: the
    option source declares its own quote basis and must not be scaled here.
    """
    if from_basis == to_basis:
        return frame.copy()
    out = frame.copy()
    absolute = {"open", "high", "low", "close", "adjusted_close", "atr",
                "atr14", "sma20", "sma50", "sma200", "recent_high",
                "resistance", "safe_strike"}
    present = absolute.intersection(out.columns)
    for idx, value in out[date_column].items():
        factor = registry.adjustment_factor(symbol, value, from_basis, to_basis)
        if from_basis == PriceBasis.ANALYTIC_ADJUSTED and to_basis == PriceBasis.MARKET_RAW:
            factor = 1.0 / factor
        for column in present:
            if pd.notna(out.at[idx, column]):
                out.at[idx, column] = float(out.at[idx, column]) * factor
    out.attrs = dict(getattr(frame, "attrs", {}))
    out.attrs.update({"price_basis": str(to_basis), "source_price_basis": str(from_basis),
                      "corporate_action_transform": "ABSOLUTE_PRICE_FIELDS_ONLY"})
    return out


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    effective_date: pd.Timestamp
    action_type: CorporateActionType
    ratio: float
    source: str
    verified: bool = False

    def __post_init__(self):
        if self.ratio <= 0:
            raise ValueError("corporate-action ratio must be positive")
        if not self.source:
            raise ValueError("corporate-action source is required")


class CorporateActionRegistry:
    """Date-level registry; no per-option lookup is required."""
    def __init__(self, actions: Iterable[CorporateAction] = ()):
        self._actions = tuple(sorted(actions, key=lambda x: (x.symbol.upper(), pd.Timestamp(x.effective_date))))

    def actions_for(self, symbol: str) -> tuple[CorporateAction, ...]:
        return tuple(x for x in self._actions if x.symbol.upper() == str(symbol).upper())

    def adjustment_factor(self, symbol: str, date, from_basis: PriceBasis, to_basis: PriceBasis) -> float:
        if from_basis == to_basis:
            return 1.0
        if PriceBasis.UNKNOWN in (from_basis, to_basis):
            raise PriceBasisError("PRICE_BASIS_UNKNOWN")
        if (from_basis, to_basis) not in {
            (PriceBasis.MARKET_RAW, PriceBasis.ANALYTIC_ADJUSTED),
            (PriceBasis.ANALYTIC_ADJUSTED, PriceBasis.MARKET_RAW),
        }:
            raise PriceBasisError(f"unsupported price-basis conversion: {from_basis}->{to_basis}")
        day = pd.Timestamp(date).normalize()
        actions = self.actions_for(symbol)
        if any(not x.verified for x in actions):
            raise PriceBasisError("CORPORATE_ACTION_UNVERIFIED")
        factor = 1.0
        for action in actions:
            if pd.Timestamp(action.effective_date).normalize() > day:
                factor *= float(action.ratio)
        return factor if from_basis == PriceBasis.MARKET_RAW else 1.0 / factor

    def to_comparison_strike(self, symbol: str, date, raw_strike: float) -> float:
        return float(raw_strike) / self.adjustment_factor(symbol, date, PriceBasis.MARKET_RAW, PriceBasis.ANALYTIC_ADJUSTED)

    def crossing_action(self, symbol: str, entry_date, expiration_date) -> CorporateAction | None:
        entry = pd.Timestamp(entry_date).normalize()
        expiry = pd.Timestamp(expiration_date).normalize()
        for action in self.actions_for(symbol):
            day = pd.Timestamp(action.effective_date).normalize()
            if entry < day <= expiry:
                return action
        return None


EMPTY_REGISTRY = CorporateActionRegistry()


def load_corporate_actions(path: str | Path = "config/data/corporate_actions.csv") -> CorporateActionRegistry:
    """Load verified actions from the authoritative versioned registry."""
    target = Path(path)
    if not target.exists():
        return EMPTY_REGISTRY
    actions = []
    with target.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("verified", "")).strip().lower() not in {"true", "1", "yes"}:
                continue
            actions.append(CorporateAction(
                symbol=row["symbol"], effective_date=pd.Timestamp(row["effective_date"]),
                action_type=CorporateActionType(row["action_type"]), ratio=float(row["ratio"]),
                source=row["source"], verified=True,
            ))
    return CorporateActionRegistry(actions)


def comparison_strike(symbol: str, date, raw_strike: float, registry: CorporateActionRegistry = EMPTY_REGISTRY) -> float:
    return registry.to_comparison_strike(symbol, date, raw_strike)
