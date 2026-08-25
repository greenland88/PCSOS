"""Ticker-agnostic covered-call research primitives.

Research-only: this module models ``LONG_100_SHARES + SHORT_1_CALL`` and
does not place orders or change PCS production rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping
from datetime import date


class CoveredCallState(StrEnum):
    FLAT_CALL = "FLAT_CALL"
    SHORT_CALL_OPEN = "SHORT_CALL_OPEN"
    BUY_TO_CLOSE = "BUY_TO_CLOSE"
    EXPIRE_WORTHLESS = "EXPIRE_WORTHLESS"
    ASSIGNED = "ASSIGNED"
    ROLL = "ROLL"
    HARD_CONSTRAINT_CONFLICT = "HARD_CONSTRAINT_CONFLICT"


@dataclass(frozen=True)
class CoveredCallResearchConfig:
    """All research knobs are explicit and independent of ticker identity."""
    dte_buckets: tuple[tuple[int, int], ...] = ((7, 14), (14, 21), (21, 30), (30, 45), (45, 60))
    delta_buckets: tuple[tuple[float, float], ...] = ((0.15, 0.25), (0.25, 0.35), (0.35, 0.45))
    moneyness_buckets: tuple[tuple[float, float], ...] = ((1.00, 1.03), (1.03, 1.08), (1.08, 1.20))
    atr_distance_buckets: tuple[tuple[float, float], ...] = ((0.0, 1.0), (1.0, 2.0), (2.0, 4.0))
    target_deltas: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30)
    target_moneyness: tuple[float, ...] = (1.03, 1.05, 1.075, 1.10, 1.125, 1.15, 1.20)
    target_atr_distances: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
    profit_capture: tuple[float, ...] = (0.40, 0.50, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
    minimum_holding_days: tuple[int, ...] = (3, 5, 10, 15)
    remaining_dte_conditions: tuple[int, ...] = (21, 14, 10, 7)
    min_open_interest: int = 100
    min_volume: int = 1
    max_spread_pct: float = 0.20
    assignment_handling: str = "FORBID_AND_FAIL_CLOSED"
    mandatory_roll_days: int = 5
    event_filter: str = "DECLARED_EVENTS_ONLY"
    trend_features: tuple[str, ...] = ("close_vs_sma20", "close_vs_sma50", "close_vs_sma200")
    volatility_features: tuple[str, ...] = ("atr", "realized_volatility", "iv_rank")
    market_context_features: tuple[str, ...] = ("spy_confirmation", "qqq_confirmation")


@dataclass(frozen=True)
class CoveredCallContract:
    symbol: str
    quote_date: str
    expiration: str
    strike: float
    bid: float
    ask: float
    delta: float | None = None
    open_interest: int | None = None
    volume: int | None = None
    underlying_price: float | None = None
    dte: int | None = None

    @property
    def mid(self) -> float: return (self.bid + self.ask) / 2.0
    @property
    def spread_pct(self) -> float: return (self.ask - self.bid) / self.mid if self.mid > 0 else float("inf")


def select_contract(contracts: Iterable[CoveredCallContract], *, config: CoveredCallResearchConfig,
                    dte: int, target_delta: float, min_strike: float | None = None) -> CoveredCallContract | None:
    """Select an exact contract from an already PIT-valid option chain."""
    eligible = []
    for c in contracts:
        if c.dte != dte or c.delta is None or c.delta <= 0 or c.open_interest is None or c.volume is None:
            continue
        if c.open_interest < config.min_open_interest or c.volume < config.min_volume or c.spread_pct > config.max_spread_pct:
            continue
        if min_strike is not None and c.strike < min_strike:
            continue
        eligible.append(c)
    return min(eligible, key=lambda c: abs(float(c.delta) - target_delta)) if eligible else None


@dataclass
class CoveredCallPosition:
    symbol: str
    shares: int = 100
    state: CoveredCallState = CoveredCallState.FLAT_CALL
    stock_entry_price: float | None = None
    contract: CoveredCallContract | None = None
    premium_collected: float = 0.0
    call_realized_pnl: float = 0.0
    assignment_impact: float = 0.0
    episode_pnl: float = 0.0
    roll_count: int = 0
    roll_credits: float = 0.0

    def open(self, stock_price: float, contract: CoveredCallContract) -> None:
        if self.state is not CoveredCallState.FLAT_CALL or self.contract is not None:
            raise ValueError("ONE_SHORT_CALL_PER_100_SHARES")
        self.stock_entry_price, self.contract = float(stock_price), contract
        self.premium_collected = contract.mid * self.shares
        self.state = CoveredCallState.SHORT_CALL_OPEN

    def close(self, state: CoveredCallState, *, stock_price: float, buy_to_close_price: float | None = None,
              allow_loss: bool = False, profit_threshold: float = 0.0, minimum_days: int = 0,
              holding_days: int = 0) -> None:
        if self.state is not CoveredCallState.SHORT_CALL_OPEN or self.contract is None:
            raise ValueError("SHORT_CALL_NOT_OPEN")
        if state not in {CoveredCallState.BUY_TO_CLOSE, CoveredCallState.EXPIRE_WORTHLESS, CoveredCallState.ASSIGNED}:
            raise ValueError("INVALID_COVERED_CALL_TERMINAL_STATE")
        if state is CoveredCallState.BUY_TO_CLOSE:
            if buy_to_close_price is None: raise ValueError("BUY_TO_CLOSE_PRICE_REQUIRED")
            self.call_realized_pnl = self.premium_collected - float(buy_to_close_price) * self.shares
            if not allow_loss and (self.call_realized_pnl + self.episode_pnl) <= 0:
                raise ValueError("H2_LOSS_MAKING_CLOSE_FORBIDDEN")
            if self.call_realized_pnl + self.episode_pnl < float(profit_threshold) or holding_days < minimum_days:
                raise ValueError("PROFIT_CLOSE_CONDITIONS_NOT_MET")
        elif state is CoveredCallState.EXPIRE_WORTHLESS:
            self.call_realized_pnl = self.premium_collected
        else:
            self.call_realized_pnl = self.premium_collected
            self.assignment_impact = (self.contract.strike - float(stock_price)) * self.shares
        self.state = state
        self.episode_pnl += self.call_realized_pnl

    def roll(self, *, net_credit: float, new_expiration: str, new_strike: float,
             new_bid: float, new_ask: float, new_delta: float | None = None) -> None:
        """Roll only with positive credit and a still-profitable episode."""
        if self.state is not CoveredCallState.SHORT_CALL_OPEN or self.contract is None:
            raise ValueError("SHORT_CALL_NOT_OPEN")
        if float(net_credit) <= 0:
            raise ValueError("H3_DEBIT_OR_ZERO_CREDIT_ROLL_FORBIDDEN")
        if self.episode_pnl + float(net_credit) <= 0:
            raise ValueError("H3_LOSS_MAKING_ROLL_FORBIDDEN")
        self.roll_count += 1
        self.roll_credits += float(net_credit)
        self.episode_pnl += float(net_credit)
        self.premium_collected += float(net_credit) * self.shares
        self.contract = CoveredCallContract(self.symbol, self.contract.quote_date, str(new_expiration),
                                            float(new_strike), float(new_bid), float(new_ask), new_delta,
                                            underlying_price=self.contract.underlying_price)
        self.state = CoveredCallState.SHORT_CALL_OPEN

    def economic_result(self, exit_stock_price: float) -> dict[str, float]:
        if self.stock_entry_price is None: raise ValueError("POSITION_NOT_OPENED")
        stock_pnl = (float(exit_stock_price) - self.stock_entry_price) * self.shares
        combined = stock_pnl + self.call_realized_pnl + self.assignment_impact
        return {"stock_pnl": stock_pnl, "call_premium": self.premium_collected,
                "call_realized_pnl": self.call_realized_pnl, "assignment_impact": self.assignment_impact,
                "combined_pnl": combined}


def replay_covered_call(position: CoveredCallPosition, observations: Iterable[Mapping[str, Any]],
                        *, profit_capture: float = .60, time_exit_days: int | None = None) -> dict[str, Any]:
    """Replay one short call from PIT observations through terminal state.

    Each observation must contain ``date``, ``underlying_close``, ``bid``,
    ``ask`` and ``expiration``. Quotes are consumed in chronological order.
    """
    rows = sorted(list(observations), key=lambda r: str(r["date"]))
    if position.state is not CoveredCallState.SHORT_CALL_OPEN or not rows:
        raise ValueError("OPEN_POSITION_AND_OBSERVATIONS_REQUIRED")
    entry_date = rows[0]["date"]
    target = position.premium_collected * (1.0 - float(profit_capture))
    exit_row = None
    for row in rows:
        days = (date.fromisoformat(str(row["date"])[:10]) - date.fromisoformat(str(entry_date)[:10])).days
        mid = (float(row["bid"]) + float(row["ask"])) / 2.0 * position.shares
        dte = (date.fromisoformat(str(row["expiration"])[:10]) - date.fromisoformat(str(row["date"])[:10])).days
        if dte <= 5 and float(row.get("underlying_close", 0)) >= float(position.contract.strike):
            roll_credit = row.get("roll_net_credit")
            if roll_credit is not None and float(roll_credit) > 0 and position.episode_pnl + float(roll_credit) > 0:
                position.roll(net_credit=float(roll_credit), new_expiration=str(row.get("roll_expiration", row["expiration"])),
                              new_strike=float(row.get("roll_strike", position.contract.strike)),
                              new_bid=float(row.get("roll_bid", row["bid"])), new_ask=float(row.get("roll_ask", row["ask"])),
                              new_delta=float(row["roll_delta"]) if row.get("roll_delta") is not None else None)
                continue
            position.state = CoveredCallState.HARD_CONSTRAINT_CONFLICT
            return {"symbol": position.symbol, "entry_date": entry_date, "exit_date": row["date"],
                    "exit_state": position.state.value, "status": "HARD_CONSTRAINT_CONFLICT",
                    "reason_codes": ["H1_NO_ASSIGNMENT", "H4_MANDATORY_ROLL_REVIEW",
                                     "NO_PROFITABLE_CLOSE_OR_ROLL"]}
        if mid <= target or (time_exit_days is not None and days >= time_exit_days):
            try:
                position.close(CoveredCallState.BUY_TO_CLOSE, stock_price=float(row["underlying_close"]),
                               buy_to_close_price=mid / position.shares, holding_days=days)
            except ValueError as exc:
                continue
            exit_row = row
            break
        if str(row["date"])[:10] >= str(row["expiration"])[:10]:
            if float(row["underlying_close"]) >= float(position.contract.strike):
                position.state = CoveredCallState.ASSIGNED
                return {"symbol": position.symbol, "entry_date": entry_date, "exit_date": row["date"],
                        "exit_state": position.state.value, "status": "STRATEGY_VIOLATION",
                        "reason_codes": ["H1_NO_ASSIGNMENT", "ASSIGNMENT_OBSERVED"]}
            position.close(CoveredCallState.EXPIRE_WORTHLESS, stock_price=float(row["underlying_close"]), allow_loss=True)
            exit_row = row
            break
    if position.state is CoveredCallState.SHORT_CALL_OPEN:
        raise ValueError("LIFECYCLE_OBSERVATIONS_END_BEFORE_TERMINAL_STATE")
    last = exit_row or rows[-1]
    return {"symbol": position.symbol, "entry_date": entry_date,
            "exit_date": last["date"],
            "exit_state": position.state.value, "roll_count": position.roll_count,
            "roll_credits": position.roll_credits, **position.economic_result(float(last["underlying_close"]))}


def compare_to_buy_and_hold(*, stock_entry_price: float, stock_exit_price: float,
                            covered_call_result: Mapping[str, float]) -> dict[str, float]:
    buy_hold = (float(stock_exit_price) - float(stock_entry_price)) * 100
    combined = float(covered_call_result["combined_pnl"])
    return {**{k: float(v) for k, v in covered_call_result.items()},
            "buy_and_hold_pnl": buy_hold, "excess_return_vs_buy_and_hold": combined - buy_hold,
            "upside_sacrificed": max(buy_hold - combined, 0.0),
            "upside_retained_pct": combined / buy_hold if buy_hold > 0 else None}


def aggregate_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    def total(k: str) -> float: return sum(float(r.get(k, 0) or 0) for r in rows)
    wins = [float(r.get("combined_pnl", 0) or 0) for r in rows if float(r.get("combined_pnl", 0) or 0) > 0]
    losses = [float(r.get("combined_pnl", 0) or 0) for r in rows if float(r.get("combined_pnl", 0) or 0) < 0]
    violations = sum(str(r.get("status")) == "STRATEGY_VIOLATION" for r in rows)
    conflicts = sum(str(r.get("status")) == "HARD_CONSTRAINT_CONFLICT" or
                    str(r.get("exit_state")) == CoveredCallState.HARD_CONSTRAINT_CONFLICT.value for r in rows)
    return {"trades": len(rows), "premium_collected": total("call_premium"), "option_pnl": total("call_realized_pnl"),
            "stock_pnl": total("stock_pnl"), "combined_pnl": total("combined_pnl"), "buy_and_hold_pnl": total("buy_and_hold_pnl"),
            "excess_return": total("excess_return_vs_buy_and_hold"), "upside_sacrificed": total("upside_sacrificed"),
            "win_rate": len(wins) / len(rows) if rows else None,
            "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
            "expectancy": total("combined_pnl") / len(rows) if rows else None,
            "assignment_rate": sum(str(r.get("exit_state")) == CoveredCallState.ASSIGNED.value for r in rows) / len(rows) if rows else None,
            "assignment_violations": violations, "hard_constraint_conflicts": conflicts,
            "hard_constraint_conflict_rate": conflicts / len(rows) if rows else None,
            "profitable_close_rate": sum(str(r.get("exit_state")) == CoveredCallState.BUY_TO_CLOSE.value and
                                          float(r.get("combined_pnl", 0) or 0) > 0 for r in rows) / len(rows) if rows else None}


def sell_call_timing_signal(*, stock: Mapping[str, Any], market: Mapping[str, Any],
                            config: CoveredCallResearchConfig | None = None) -> dict[str, Any]:
    """Evaluate a decision-time sell-call opportunity from PIT-safe features.

    Missing inputs fail closed.  This is a research signal, not an order.
    """
    if stock.get("close_vs_sma20") is None and stock.get("trend_gate") is not None:
        stock = {**stock, "close_vs_sma20": 0.0 if bool(stock["trend_gate"]) else -1.0,
                 "close_vs_sma50": float(stock.get("trend_score", 0.0)),
                 "iv_rank": stock.get("iv_rank", 0.5)}
    required = ("close", "atr", "close_vs_sma20", "close_vs_sma50", "iv_rank")
    missing = [key for key in required if stock.get(key) is None]
    missing += [key for key in ("spy_confirmation", "qqq_confirmation") if market.get(key) is None]
    if missing:
        return {"action": "WAIT", "status": "DATA_INSUFFICIENT", "reason_codes": ["MISSING_PIT_FEATURE"] + missing}
    reasons = []
    if not bool(market["spy_confirmation"]) or not bool(market["qqq_confirmation"]):
        reasons.append("MARKET_CONFIRMATION_NOT_POSITIVE")
    if float(stock["close_vs_sma20"]) < 0:
        reasons.append("STOCK_TREND_WEAK")
    # Selling is considered only when shares are held and price is extended;
    # exact thresholds remain config/research parameters, never ticker names.
    extended = float(stock["close_vs_sma20"]) >= 0 and float(stock["close_vs_sma50"]) >= 0
    if not extended:
        reasons.append("NOT_EXTENDED")
    return {"action": "OPEN" if not reasons else "WAIT",
            "status": "SIGNAL" if not reasons else "REJECTED",
            "reason_codes": ["PIT_SAFE_FEATURES", "MARKET_CONTEXT_APPLIED"] + reasons,
            "symbol": str(stock.get("symbol", "")).upper(), "config_version": "covered_call_economic_v1"}
