"""Research-only cash-secured-put primitives.

This module is deliberately data-source agnostic: callers supply PIT rows
obtained through ``PCSDataAccess`` and the unified research runner.  It never
reads parquet, places orders, or changes PCS production rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Any, Iterable, Mapping


from pcs.research.research_framework import StrategyType


class PutLifecycleState(StrEnum):
    OPEN = "OPEN"
    HOLD = "HOLD"
    PROFIT_CLOSE = "PROFIT_CLOSE"
    RISK_CLOSE = "RISK_CLOSE"
    ROLL_DOWN_OUT = "ROLL_DOWN_OUT"  # legacy audit label; position remains active after roll
    EXPIRE_WORTHLESS = "EXPIRE_WORTHLESS"
    ASSIGNMENT = "ASSIGNMENT"


@dataclass(frozen=True)
class ShortPutContract:
    symbol: str
    quote_date: str
    expiration: str
    strike: float
    bid: float
    ask: float
    delta: float | None
    iv: float | None
    open_interest: int | None
    volume: int | None
    underlying_price: float
    atr: float
    support: float | None
    pit_status: str = "PIT_SAFE"

    @property
    def dte(self) -> int:
        from datetime import date
        return (date.fromisoformat(self.expiration[:10]) - date.fromisoformat(self.quote_date[:10])).days

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_pct(self) -> float:
        return self.spread / ((self.bid + self.ask) / 2) if self.bid + self.ask > 0 else float("inf")

    @property
    def atr_distance(self) -> float:
        return (self.underlying_price - self.strike) / self.atr if self.atr > 0 else float("nan")

    @property
    def credit(self) -> float:
        return self.bid * 100

    @property
    def collateral_required(self) -> float:
        return self.strike * 100 - self.credit


@dataclass(frozen=True)
class PutSelection:
    contract: ShortPutContract | None
    candidates: tuple[dict[str, Any], ...]
    reason_codes: tuple[str, ...]
    selected_reason: str | None = None


@dataclass(frozen=True)
class ShortPutSelectorConfig:
    dte_min: int = 7
    dte_max: int = 45
    abs_delta_min: float = .08
    abs_delta_max: float = .35
    atr_distance_min: float = 1.0
    atr_distance_max: float = 3.5
    max_spread_pct: float = .20
    min_open_interest: int = 100
    min_volume: int = 1


class ShortPutContractSelector:
    def __init__(self, config: ShortPutSelectorConfig = ShortPutSelectorConfig()):
        self.config = config

    def select(self, quotes: Iterable[ShortPutContract], *, available_cash: float,
               max_assignment_shares: int, current_soxl_risk: float = 0,
               max_soxl_risk: float = float("inf"), open_positions: int = 0,
               max_positions: int = 1) -> PutSelection:
        reasons: list[str] = []
        if available_cash < 0: reasons.append("AVAILABLE_CASH_INVALID")
        if max_assignment_shares < 100: reasons.append("MAX_ASSIGNMENT_SHARES_INSUFFICIENT")
        if current_soxl_risk > max_soxl_risk: reasons.append("SOXL_RISK_LIMIT_EXCEEDED")
        if open_positions >= max_positions: reasons.append("MAX_SIMULTANEOUS_POSITIONS_REACHED")
        rows: list[tuple[ShortPutContract, dict[str, Any]]] = []
        for c in quotes:
            rejected: list[str] = []
            vals = (c.bid, c.ask, c.strike, c.underlying_price, c.atr)
            if c.symbol.upper() != "SOXL": rejected.append("UNDERLYING_MISMATCH")
            if c.pit_status != "PIT_SAFE": rejected.append("QUOTE_NOT_PIT_SAFE")
            if any(not isfinite(float(x)) for x in vals) or c.bid <= 0 or c.ask < c.bid: rejected.append("INVALID_BID_ASK")
            if not self.config.dte_min <= c.dte <= self.config.dte_max: rejected.append("DTE_OUT_OF_RANGE")
            if c.delta is None or not self.config.abs_delta_min <= abs(float(c.delta)) <= self.config.abs_delta_max: rejected.append("DELTA_OUT_OF_RANGE")
            if not self.config.atr_distance_min <= c.atr_distance <= self.config.atr_distance_max: rejected.append("ATR_DISTANCE_OUT_OF_RANGE")
            if c.support is None or c.strike >= c.support: rejected.append("STRUCTURAL_SUPPORT_DISTANCE_INVALID")
            if c.iv is None or not isfinite(float(c.iv)): rejected.append("IV_MISSING")
            if c.open_interest is None or c.open_interest < self.config.min_open_interest: rejected.append("OI_INSUFFICIENT")
            if c.volume is None or c.volume < self.config.min_volume: rejected.append("VOLUME_INSUFFICIENT")
            if c.spread_pct > self.config.max_spread_pct: rejected.append("SPREAD_TOO_WIDE")
            if c.collateral_required > available_cash: rejected.append("AVAILABLE_CASH_INSUFFICIENT")
            if c.collateral_required > max_soxl_risk - current_soxl_risk: rejected.append("ASSIGNMENT_RISK_LIMIT_EXCEEDED")
            row = {"expiration": c.expiration, "strike": c.strike, "dte": c.dte, "delta": c.delta,
                   "bid": c.bid, "ask": c.ask, "spread_pct": c.spread_pct, "iv": c.iv,
                   "open_interest": c.open_interest, "volume": c.volume, "credit": c.credit,
                   "collateral_required": c.collateral_required, "atr_distance": c.atr_distance,
                   "eligible": not rejected, "reason_codes": rejected}
            rows.append((c, row))
        eligible = [(c, row) for c, row in rows if row["eligible"]]
        if reasons or not eligible:
            return PutSelection(None, tuple(x[1] for x in rows), tuple(reasons or ["NO_LIQUIDITY_ELIGIBLE_PUT"]), None)
        chosen = min(eligible, key=lambda x: (abs(abs(float(x[0].delta)) - .20), abs(x[0].dte - 21), -x[0].bid))
        return PutSelection(chosen[0], tuple(x[1] for x in rows), ("PIT_QUOTE_VALIDATED", "DYNAMIC_STRIKE_SELECTED"), "closest_delta_then_dte_then_bid")


@dataclass(frozen=True)
class AssignmentLedger:
    assignment_price: float
    stock_mark: float
    shares_acquired: int
    option_credit: float
    adjusted_stock_cost_basis: float
    stock_mtm: float
    total_economic_pnl: float
    capital_occupied_days: int


@dataclass
class CashSecuredPutPosition:
    contract: ShortPutContract
    entry_credit: float
    state: PutLifecycleState = PutLifecycleState.OPEN
    roll_count: int = 0
    assignment: AssignmentLedger | None = None
    roll_history: list[dict[str, Any]] = field(default_factory=list)
    cumulative_credit: float = 0.0
    max_rolls: int = 3

    def collateral(self) -> float:
        return self.contract.strike * 100 - self.entry_credit * 100

    def close(self, buyback_ask: float) -> float:
        if self.state not in {PutLifecycleState.OPEN, PutLifecycleState.HOLD}: raise ValueError("PUT_NOT_OPEN")
        if buyback_ask < 0: raise ValueError("INVALID_BUYBACK_ASK")
        self.state = PutLifecycleState.PROFIT_CLOSE if buyback_ask < self.entry_credit else PutLifecycleState.RISK_CLOSE
        return (self.entry_credit - buyback_ask) * 100

    def hold(self) -> None:
        if self.state not in {PutLifecycleState.OPEN, PutLifecycleState.HOLD}:
            raise ValueError("PUT_NOT_OPEN")
        self.state = PutLifecycleState.HOLD

    def roll_down_out(self, new_contract: ShortPutContract, old_buyback_ask: float) -> float:
        """Replace the put only with exact quotes and a net positive credit."""
        if self.state not in {PutLifecycleState.OPEN, PutLifecycleState.HOLD}:
            raise ValueError("PUT_NOT_OPEN")
        if self.roll_count >= int(self.max_rolls):
            raise ValueError("MAX_ROLLS_EXCEEDED")
        if new_contract.expiration <= self.contract.expiration or new_contract.strike >= self.contract.strike:
            raise ValueError("ROLL_MUST_BE_DOWN_AND_OUT")
        if old_buyback_ask <= 0 or new_contract.bid <= 0 or new_contract.ask < new_contract.bid:
            raise ValueError("ROLL_EXACT_QUOTES_REQUIRED")
        credit = (new_contract.bid - float(old_buyback_ask)) * 100
        if credit <= 0:
            raise ValueError("ROLL_REJECT_DEBIT")
        old_contract = self.contract
        self.entry_credit += credit / 100
        self.cumulative_credit += credit / 100
        self.contract = new_contract
        self.roll_count += 1
        self.roll_history.append({"from_expiration": old_contract.expiration, "from_strike": old_contract.strike,
                                  "to_expiration": new_contract.expiration, "to_strike": new_contract.strike,
                                  "old_buyback_ask": float(old_buyback_ask), "new_bid": float(new_contract.bid),
                                  "net_credit": credit / 100, "roll_number": self.roll_count})
        self.state = PutLifecycleState.HOLD
        return credit

    def expire(self, underlying_close: float, days_held: int) -> float:
        if underlying_close < self.contract.strike: return self.assign(underlying_close, days_held)
        self.state = PutLifecycleState.EXPIRE_WORTHLESS
        return self.entry_credit * 100

    def assign(self, stock_mark: float, days_held: int) -> AssignmentLedger:
        self.state = PutLifecycleState.ASSIGNMENT
        basis = self.contract.strike - self.entry_credit
        self.assignment = AssignmentLedger(self.contract.strike, float(stock_mark), 100, self.entry_credit * 100,
                                            basis, (float(stock_mark) - basis) * 100,
                                            (float(stock_mark) - basis) * 100,
                                            int(days_held))
        return self.assignment
