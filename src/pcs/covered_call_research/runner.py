"""Independent long-stock covered-call replay primitives.

This module deliberately does not import PCS research specs, put selectors, or
PCS lifecycle code.  It models only the user's long-stock call overlay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping
from uuid import uuid4


class Action(str, Enum):
    HOLD = "HOLD"
    BUY_TO_CLOSE = "BUY_TO_CLOSE"
    ROLL = "ROLL"
    EXPIRE = "EXPIRE"


class ReplayStatus(str, Enum):
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class CallQuote:
    symbol: str
    trade_date: str
    expiration: str
    strike: float
    bid: float | None
    ask: float | None
    delta: float | None = None
    iv: float | None = None
    open_interest: float | None = None
    volume: float | None = None

    @property
    def identity(self) -> str:
        # Trade date identifies the observation, not the option contract.
        # Keeping it out of the contract identity is required to match the
        # same listed contract across daily quotes and a same-day roll.
        return f"{self.symbol.upper()}|{self.expiration[:10]}|{self.strike:.8f}|C"

    def require_bid(self) -> float:
        if self.bid is None or self.bid < 0:
            raise ValueError(f"MISSING_EXECUTABLE_BID:{self.identity}")
        return float(self.bid)

    def require_ask(self) -> float:
        if self.ask is None or self.ask < 0:
            raise ValueError(f"MISSING_EXECUTABLE_ASK:{self.identity}")
        return float(self.ask)


@dataclass
class CallLot:
    lot_id: str
    opened_date: str
    expiration: str
    strike: float
    shares: int
    premium_received: float
    current_quote_identity: str
    buyback_cost: float = 0.0
    roll_debit: float = 0.0
    roll_credit: float = 0.0
    assigned_risk_events: int = 0
    itm_days: int = 0
    closed_date: str | None = None
    action: Action = Action.HOLD

    @property
    def overlay_pnl(self) -> float:
        return self.premium_received - self.buyback_cost + self.roll_credit - self.roll_debit


@dataclass
class ReplayResult:
    symbol: str
    status: ReplayStatus
    actions: list[dict[str, Any]] = field(default_factory=list)
    lots: list[CallLot] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)

    def metrics(self) -> dict[str, Any]:
        lots = self.lots
        return {
            "calls_opened": len(lots),
            "premium_received": sum(x.premium_received for x in lots),
            "buyback_cost": sum(x.buyback_cost for x in lots),
            "roll_credit": sum(x.roll_credit for x in lots),
            "roll_debit": sum(x.roll_debit for x in lots),
            "call_overlay_pnl": sum(x.overlay_pnl for x in lots),
            "itm_events": sum(x.itm_days for x in lots),
            "assignment_risk_events": sum(x.assigned_risk_events for x in lots),
        }


class CoveredCallRunner:
    """Small deterministic state machine for a long-stock call overlay."""

    def __init__(self, symbol: str, *, shares_per_call: int = 100,
                 max_short_calls: int = 3, commission_per_contract: float = 0.0,
                 slippage_per_contract: float = 0.0):
        if shares_per_call != 100 or max_short_calls < 1:
            raise ValueError("INVALID_POSITION_CAPACITY")
        self.symbol = symbol.upper()
        self.shares_per_call = shares_per_call
        self.max_short_calls = max_short_calls
        self.commission_per_contract = float(commission_per_contract)
        self.slippage_per_contract = float(slippage_per_contract)
        self.lots: list[CallLot] = []
        self.actions: list[dict[str, Any]] = []
        self.blockers: list[str] = []

    def open_call(self, quote: CallQuote) -> CallLot:
        if quote.symbol.upper() != self.symbol:
            raise ValueError("TICKER_ISOLATION_FAILURE")
        if len([x for x in self.lots if x.closed_date is None]) >= self.max_short_calls:
            raise ValueError("CALL_CAPACITY_EXCEEDED")
        premium = quote.require_bid() * self.shares_per_call - self.commission_per_contract - self.slippage_per_contract
        lot = CallLot(uuid4().hex, quote.trade_date, quote.expiration, float(quote.strike),
                      self.shares_per_call, premium, quote.identity)
        self.lots.append(lot)
        self.actions.append({"date": quote.trade_date, "action": "HOLD", "event": "OPEN_CALL",
                             "lot_id": lot.lot_id, "contract": quote.identity, "execution": "BID"})
        return lot

    def buy_to_close(self, lot: CallLot, quote: CallQuote) -> None:
        if quote.identity != lot.current_quote_identity and quote.strike != lot.strike:
            raise ValueError("EXACT_CONTRACT_IDENTITY_MISMATCH")
        cost = quote.require_ask() * lot.shares + self.commission_per_contract + self.slippage_per_contract
        lot.buyback_cost += cost
        lot.closed_date = quote.trade_date
        lot.action = Action.BUY_TO_CLOSE
        self.actions.append({"date": quote.trade_date, "action": Action.BUY_TO_CLOSE.value,
                             "lot_id": lot.lot_id, "contract": quote.identity, "cost": cost, "execution": "ASK"})

    def roll(self, lot: CallLot, old_quote: CallQuote, new_quote: CallQuote) -> None:
        if old_quote.trade_date[:10] != new_quote.trade_date[:10]:
            raise ValueError("ROLL_LEGS_NOT_SAME_DAY")
        if old_quote.identity != lot.current_quote_identity:
            raise ValueError("EXACT_OLD_CONTRACT_IDENTITY_MISMATCH")
        buyback = old_quote.require_ask() * lot.shares + self.commission_per_contract + self.slippage_per_contract
        proceeds = new_quote.require_bid() * lot.shares - self.commission_per_contract - self.slippage_per_contract
        net = proceeds - buyback
        if net >= 0: lot.roll_credit += net
        else: lot.roll_debit += -net
        lot.buyback_cost += buyback
        lot.premium_received += proceeds
        lot.expiration, lot.strike = new_quote.expiration, float(new_quote.strike)
        lot.current_quote_identity = new_quote.identity
        lot.action = Action.ROLL
        self.actions.append({"date": new_quote.trade_date, "action": Action.ROLL.value,
                             "lot_id": lot.lot_id, "old_contract": old_quote.identity,
                             "new_contract": new_quote.identity, "buyback_ask": buyback,
                             "new_sale_bid": proceeds, "net_roll": net})

    def observe(self, date: str, underlying_close: float, quote_by_lot: Mapping[str, CallQuote]) -> None:
        for lot in self.lots:
            if lot.closed_date is not None: continue
            quote = quote_by_lot.get(lot.lot_id)
            if quote is None:
                self.blockers.append(f"MISSING_EXACT_CONTRACT_QUOTE:{date}:{lot.current_quote_identity}")
                continue
            if underlying_close >= lot.strike:
                lot.itm_days += 1
                if date[:10] >= lot.expiration[:10]:
                    lot.assigned_risk_events += 1
                    self.blockers.append(f"ITM_EXPIRY_ASSIGNMENT_RISK:{date}:{lot.current_quote_identity}")
            self.actions.append({"date": date, "action": Action.HOLD.value, "lot_id": lot.lot_id,
                                 "contract": quote.identity, "underlying_close": underlying_close})

    def result(self) -> ReplayResult:
        return ReplayResult(self.symbol, ReplayStatus.BLOCKED if self.blockers else ReplayStatus.COMPLETE,
                            self.actions, self.lots, sorted(set(self.blockers)))
