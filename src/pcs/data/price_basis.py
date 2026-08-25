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


class PriceBasis(StrEnum):
    MARKET_RAW = "MARKET_RAW"
    ANALYTIC_ADJUSTED = "ANALYTIC_ADJUSTED"
    UNKNOWN = "UNKNOWN"


class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"


class PriceBasisError(ValueError):
    pass


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
    def __init__(self, actions: Iterable[CorporateAction] = (), *, covered_symbols: Iterable[str] | None = None):
        self._actions = tuple(sorted(actions, key=lambda x: (x.symbol.upper(), pd.Timestamp(x.effective_date))))
        self._covered_symbols = (None if covered_symbols is None
                                 else frozenset(str(x).upper() for x in covered_symbols))

    def actions_for(self, symbol: str) -> tuple[CorporateAction, ...]:
        return tuple(x for x in self._actions if x.symbol.upper() == str(symbol).upper())

    def adjustment_factor(self, symbol: str, date, from_basis: PriceBasis, to_basis: PriceBasis) -> float:
        if from_basis == to_basis:
            return 1.0
        if PriceBasis.UNKNOWN in (from_basis, to_basis):
            raise PriceBasisError("PRICE_BASIS_UNKNOWN")
        if (from_basis, to_basis) != (PriceBasis.MARKET_RAW, PriceBasis.ANALYTIC_ADJUSTED):
            raise PriceBasisError(f"unsupported price-basis conversion: {from_basis}->{to_basis}")
        day = pd.Timestamp(date).normalize()
        if self._covered_symbols is not None and str(symbol).upper() not in self._covered_symbols:
            raise PriceBasisError("CORPORATE_ACTION_COVERAGE_UNPROVEN")
        actions = self.actions_for(symbol)
        if any(not x.verified for x in actions):
            raise PriceBasisError("CORPORATE_ACTION_UNVERIFIED")
        factor = 1.0
        for action in actions:
            if pd.Timestamp(action.effective_date).normalize() > day:
                factor *= float(action.ratio)
        return factor

    def to_comparison_strike(self, symbol: str, date, raw_strike: float) -> float:
        return float(raw_strike) / self.adjustment_factor(symbol, date, PriceBasis.MARKET_RAW, PriceBasis.ANALYTIC_ADJUSTED)

    def crossing_action(self, symbol: str, entry_date, expiration_date) -> CorporateAction | None:
        if self._covered_symbols is not None and str(symbol).upper() not in self._covered_symbols:
            raise PriceBasisError("CORPORATE_ACTION_COVERAGE_UNPROVEN")
        entry = pd.Timestamp(entry_date).normalize()
        expiry = pd.Timestamp(expiration_date).normalize()
        for action in self.actions_for(symbol):
            day = pd.Timestamp(action.effective_date).normalize()
            if entry < day <= expiry:
                if not action.verified:
                    raise PriceBasisError("CORPORATE_ACTION_UNVERIFIED")
                return action
        return None


EMPTY_REGISTRY = CorporateActionRegistry()


def load_corporate_actions(path: str | Path = "config/data/corporate_actions.csv") -> CorporateActionRegistry:
    """Load all declared actions; unverified rows remain fail-closed inputs."""
    target = Path(path)
    if not target.exists():
        return EMPTY_REGISTRY
    actions = []
    with target.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            verified = str(row.get("verified", "")).strip().lower() in {"true", "1", "yes"}
            actions.append(CorporateAction(
                symbol=row["symbol"], effective_date=pd.Timestamp(row["effective_date"]),
                action_type=CorporateActionType(row["action_type"]), ratio=float(row["ratio"]),
                source=row["source"], verified=verified,
            ))
    # A registry loaded from a finite file cannot prove absence for symbols
    # omitted from that file.  Optional coverage_symbols makes that boundary
    # explicit; absent coverage defaults to the symbols actually declared.
    declared = set()
    with target.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            declared.add(str(row["symbol"]).upper())
    return CorporateActionRegistry(actions, covered_symbols=declared)


def comparison_strike(symbol: str, date, raw_strike: float, registry: CorporateActionRegistry = EMPTY_REGISTRY) -> float:
    return registry.to_comparison_strike(symbol, date, raw_strike)
