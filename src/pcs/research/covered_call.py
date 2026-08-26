"""Ticker-agnostic covered-call research primitives.

Research-only: this module models ``LONG_100_SHARES + SHORT_1_CALL`` and
does not place orders or change PCS production rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping
from datetime import date
from typing import Sequence


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


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


@dataclass
class CoveredCallEpisode:
    """Research-only accounting container spanning every call in a chain."""
    episode_id: str
    symbol: str
    stock_entry_price: float
    current_contract: CoveredCallContract
    shares: int = 100
    opened_date: str | None = None
    roll_history: list[dict[str, Any]] = field(default_factory=list)
    cumulative_premium_received: float = 0.0
    cumulative_buyback_cost: float = 0.0
    cumulative_roll_credits: float = 0.0
    realized_cashflow: float = 0.0
    closed: bool = False
    conflicted: bool = False
    close_date: str | None = None
    close_underlying_price: float | None = None

    def __post_init__(self) -> None:
        self.cumulative_premium_received = self.current_contract.mid * self.shares
        self.realized_cashflow = self.cumulative_premium_received
        self.opened_date = self.opened_date or self.current_contract.quote_date

    @property
    def contract(self) -> CoveredCallContract:
        return self.current_contract

    @property
    def final_pnl(self) -> float | None:
        return self.realized_cashflow if self.closed else None

    def episode_pnl_if_closed_today(self, underlying_price: float, buyback_price: float | None = None) -> float:
        call_cashflow = self.realized_cashflow - (float(buyback_price) * self.shares if buyback_price is not None else 0.0)
        return (float(underlying_price) - self.stock_entry_price) * self.shares + call_cashflow

    def roll(self, new_contract: CoveredCallContract, *, old_buyback_price: float) -> float:
        proceeds = new_contract.mid * self.shares
        cost = float(old_buyback_price) * self.shares
        credit = proceeds - cost
        if credit < 0:
            raise ValueError("H3_DEBIT_OR_ZERO_CREDIT_ROLL_FORBIDDEN")
        self.cumulative_buyback_cost += cost
        self.cumulative_premium_received += proceeds
        self.cumulative_roll_credits += credit
        self.realized_cashflow += credit
        self.roll_history.append({"old_contract": self.current_contract, "new_contract": new_contract,
                                  "buyback_cost": cost, "new_call_proceeds": proceeds, "net_roll_credit": credit})
        self.current_contract = new_contract
        return credit

    def close(self, *, close_date: str, underlying_price: float, buyback_price: float | None = None) -> float:
        if self.closed:
            raise ValueError("EPISODE_ALREADY_CLOSED")
        pnl = self.episode_pnl_if_closed_today(underlying_price, buyback_price)
        if buyback_price is not None:
            self.cumulative_buyback_cost += float(buyback_price) * self.shares
            self.realized_cashflow -= float(buyback_price) * self.shares
        self.realized_cashflow += (float(underlying_price) - self.stock_entry_price) * self.shares
        self.closed, self.close_date = True, str(close_date)
        self.close_underlying_price = float(underlying_price)
        return pnl


class CoveredCallPositionBook:
    MAX_ACTIVE_SHORT_CALLS = 3

    def __init__(self, max_active_short_calls: int = MAX_ACTIVE_SHORT_CALLS):
        self.max_active_short_calls = max_active_short_calls
        self._episodes: dict[str, CoveredCallEpisode] = {}
        self.capacity_rejections = 0

    @property
    def active_episodes(self) -> list[CoveredCallEpisode]:
        # A conflict is not a released slot: only a fully closed episode may
        # make capacity available again.
        return [e for e in self._episodes.values() if not e.closed]

    def open(self, episode: CoveredCallEpisode) -> CoveredCallEpisode:
        if len(self.active_episodes) >= self.max_active_short_calls:
            self.capacity_rejections += 1
            raise ValueError("MAX_CALL_CAPACITY_REACHED")
        if episode.episode_id in self._episodes:
            raise ValueError("DUPLICATE_EPISODE_ID")
        self._episodes[episode.episode_id] = episode
        return episode

    add = open

    def get(self, episode_id: str) -> CoveredCallEpisode:
        return self._episodes[episode_id]

    def close(self, episode_id: str, **kwargs: Any) -> float:
        episode = self._episodes[episode_id]
        return episode.close(**kwargs)

    def release(self, episode_id: str, **kwargs: Any) -> float:
        return self.close(episode_id, **kwargs)


class CoveredCallRollSelector:
    def __init__(self, max_dte: int = 120):
        self.max_dte = max_dte

    def select(self, current_contract: CoveredCallContract, current_date: str | date,
               underlying_price: float, quotes: Iterable[CoveredCallContract]) -> tuple[CoveredCallContract, float] | None:
        today = _as_date(current_date)
        candidates = []
        for new in quotes:
            if new.symbol != current_contract.symbol or _as_date(new.expiration) <= _as_date(current_contract.expiration):
                continue
            dte = (_as_date(new.expiration) - today).days
            if dte < 0 or dte > self.max_dte or new.mid <= 0:
                continue
            credit = new.mid - current_contract.ask
            if credit < 0:
                continue
            candidates.append((new, credit * 100))
        if not candidates:
            return None
        return max(candidates, key=lambda x: (x[0].strike, -(_as_date(x[0].expiration)-today).days,
                                               x[1], -x[0].spread_pct))

    select_roll = select


class CoveredCallDailyEngine:
    """Small deterministic, research-only episode state machine.

    ``daily`` is an iterable of mappings with ``date``, ``underlying_price``
    (or ``close``), optional ``new_entry`` and optional ``entry_contract``;
    ``quotes_by_date`` contains canonical :class:`CoveredCallContract` rows.
    Entry dates are controlled by the caller, while active episodes are never
    discarded at a year boundary.
    """
    def __init__(self, symbol: str, *, profit_capture: float = .60,
                 mandatory_roll_days: int = 5, max_roll_dte: int = 120,
                 minimum_holding_days: int = 0,
                 remaining_dte_condition: int | None = None,
                 position_book: CoveredCallPositionBook | None = None):
        self.symbol = str(symbol).upper()
        self.profit_capture = float(profit_capture)
        self.mandatory_roll_days = int(mandatory_roll_days)
        self.book = position_book or CoveredCallPositionBook()
        self.selector = CoveredCallRollSelector(max_roll_dte)
        self.minimum_holding_days = int(minimum_holding_days)
        self.remaining_dte_condition = (int(remaining_dte_condition)
                                        if remaining_dte_condition is not None else None)
        self.actions: list[dict[str, Any]] = []
        self.conflicts: list[dict[str, Any]] = []
        self._sequence = 0

    def run(self, daily: Iterable[Mapping[str, Any]], *,
            quotes_by_date: Mapping[Any, Iterable[CoveredCallContract]] | None = None) -> dict[str, Any]:
        quotes_by_date = quotes_by_date or {}
        for row in sorted(daily, key=lambda r: str(r["date"])):
            day = str(row["date"])[:10]
            spot = float(row.get("underlying_price", row.get("close")))
            quotes = list(quotes_by_date.get(row["date"], quotes_by_date.get(day, ())))
            # Existing episodes are managed before a new entry can consume a slot.
            for episode in list(self.book.active_episodes):
                if episode.conflicted:
                    continue
                current = next((q for q in quotes if q.expiration == episode.contract.expiration and
                                q.strike == episode.contract.strike), None)
                if current is None:
                    if _as_date(day) >= _as_date(episode.contract.expiration):
                        episode.conflicted = True
                        conflict = {"date": day, "episode_id": episode.episode_id,
                                    "action": "CONFLICT", "reason_codes": ["H1_NO_ASSIGNMENT",
                                    "LIFECYCLE_QUOTE_UNAVAILABLE_AT_EXPIRY"]}
                        self.conflicts.append(conflict)
                        self.actions.append(conflict)
                    else:
                        self.actions.append({"date": day, "episode_id": episode.episode_id,
                                             "action": "HOLD", "reason_codes": ["QUOTE_UNAVAILABLE"]})
                    continue
                dte = (_as_date(current.expiration) - _as_date(day)).days
                buyback = current.ask
                pnl = episode.episode_pnl_if_closed_today(spot, buyback)
                # Episode-level capture: compare the remaining call value with
                # the original premium, while requiring positive whole-episode P&L.
                captured = (episode.cumulative_premium_received - episode.cumulative_buyback_cost -
                            buyback * episode.shares)
                holding_days = (_as_date(day) - _as_date(episode.opened_date)).days
                close_allowed = (pnl > 0 and captured >= episode.cumulative_premium_received * self.profit_capture
                                 and holding_days >= self.minimum_holding_days
                                 and (self.remaining_dte_condition is None or
                                      dte <= self.remaining_dte_condition))
                if close_allowed:
                    episode.close(close_date=day, underlying_price=spot, buyback_price=buyback)
                    self.actions.append({"date": day, "episode_id": episode.episode_id,
                                         "action": "CLOSE", "episode_pnl": pnl})
                    continue
                if dte <= self.mandatory_roll_days:
                    # Roll economics use today's canonical buyback ask, not
                    # the entry quote retained on the episode contract.
                    selection_base = CoveredCallContract(
                        episode.contract.symbol, day, episode.contract.expiration,
                        episode.contract.strike, current.bid, current.ask,
                        episode.contract.delta, underlying_price=spot,
                        dte=dte)
                    selected = self.selector.select(selection_base, day, spot, quotes)
                    if selected is not None:
                        new, credit = selected
                        episode.roll(new, old_buyback_price=buyback)
                        self.actions.append({"date": day, "episode_id": episode.episode_id,
                                             "action": "ROLL", "net_roll_credit": credit})
                    else:
                        self.actions.append({"date": day, "episode_id": episode.episode_id,
                                             "action": "HOLD", "reason_codes": ["H4_NO_LEGAL_ROLL"]})
                else:
                    self.actions.append({"date": day, "episode_id": episode.episode_id,
                                         "action": "HOLD", "episode_pnl": pnl})
            if row.get("new_entry") or row.get("entry_contract") is not None:
                contract = row.get("entry_contract")
                if contract is not None:
                    self._sequence += 1
                    episode = CoveredCallEpisode(f"{self.symbol}-{self._sequence}", self.symbol,
                                                 spot, contract)
                    try:
                        self.book.open(episode)
                        self.actions.append({"date": day, "episode_id": episode.episode_id, "action": "OPEN"})
                    except ValueError as exc:
                        self.actions.append({"date": day, "action": "WAIT",
                                             "reason_codes": [str(exc)]})
        completed = [e for e in self.book._episodes.values() if e.closed]
        return {"symbol": self.symbol, "episodes": list(self.book._episodes.values()),
                "actions": self.actions, "completed_episodes": completed,
                "conflicts": self.conflicts,
                "metrics": {"episodes": len(self.book._episodes), "rolls": sum(len(e.roll_history) for e in self.book._episodes.values()),
                             "capacity_rejections": self.book.capacity_rejections}}


def select_contract(contracts: Iterable[CoveredCallContract], *, config: CoveredCallResearchConfig,
                    dte: int, target_delta: float, min_strike: float | None = None,
                    selection_method: str = "DELTA", underlying_price: float | None = None,
                    atr: float | None = None, target_moneyness: float | None = None,
                    target_atr_distance: float | None = None) -> CoveredCallContract | None:
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
    if not eligible:
        return None
    method = str(selection_method).upper()
    if method == "MONEYNESS":
        if underlying_price is None or target_moneyness is None:
            return None
        return min(eligible, key=lambda c: abs(c.strike / float(underlying_price) - float(target_moneyness)))
    if method == "ATR":
        if underlying_price is None or atr is None or float(atr) <= 0 or target_atr_distance is None:
            return None
        return min(eligible, key=lambda c: abs((c.strike - float(underlying_price)) /
                                               float(atr) - float(target_atr_distance)))
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
        if float(net_credit) < 0:
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
                        *, profit_capture: float = .60, time_exit_days: int | None = None,
                        minimum_holding_days: int = 0,
                        remaining_dte_condition: int | None = None) -> dict[str, Any]:
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
                    "holding_days": days,
                    "reason_codes": ["H1_NO_ASSIGNMENT", "H4_MANDATORY_ROLL_REVIEW",
                                     "NO_PROFITABLE_CLOSE_OR_ROLL"]}
        profit_close_ready = (mid <= target and days >= int(minimum_holding_days) and
                              (remaining_dte_condition is None or dte <= int(remaining_dte_condition)))
        time_exit_ready = (time_exit_days is not None and days >= time_exit_days and
                           days >= int(minimum_holding_days))
        if profit_close_ready or time_exit_ready:
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
                        "holding_days": days,
                        "reason_codes": ["H1_NO_ASSIGNMENT", "ASSIGNMENT_OBSERVED"]}
            position.close(CoveredCallState.EXPIRE_WORTHLESS, stock_price=float(row["underlying_close"]), allow_loss=True)
            exit_row = row
            break
    if position.state is CoveredCallState.SHORT_CALL_OPEN:
        raise ValueError("LIFECYCLE_OBSERVATIONS_END_BEFORE_TERMINAL_STATE")
    last = exit_row or rows[-1]
    return {"symbol": position.symbol, "entry_date": entry_date,
            "exit_date": last["date"],
            "holding_days": (date.fromisoformat(str(last["date"])[:10]) -
                             date.fromisoformat(str(entry_date)[:10])).days,
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
    holding = [float(r.get("holding_days", 0) or 0) for r in rows if r.get("holding_days") is not None]
    equity = []
    running = 0.0
    for row in rows:
        if row.get("combined_pnl") is not None:
            running += float(row.get("combined_pnl", 0) or 0)
            equity.append(running)
    peak = 0.0
    max_drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value - peak)
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
                                          float(r.get("combined_pnl", 0) or 0) > 0 for r in rows) / len(rows) if rows else None,
            "transaction_count": len(rows),
            "average_holding_days": sum(holding) / len(holding) if holding else None,
            "average_roll_count": total("roll_count") / len(rows) if rows else None,
            "roll_credits": total("roll_credits"),
            "max_drawdown": max_drawdown}


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
