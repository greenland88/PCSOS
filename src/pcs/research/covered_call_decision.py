"""Transparent daily covered-call decision layer (research/read-only)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from enum import StrEnum
from typing import Any, Iterable, Mapping
from collections import defaultdict
import pandas as pd

from .covered_call import CoveredCallContract, CoveredCallEpisode, CoveredCallRollSelector
from .covered_call_profiles import ProfileStatus, resolve_covered_call_profile, CoveredCallProfile


class CallDecision(StrEnum):
    SELL = "SELL"
    WAIT = "WAIT"
    NO_SELL = "NO_SELL"


class PositionDecision(StrEnum):
    HOLD = "HOLD"
    ROLL = "ROLL"
    CLOSE = "CLOSE"


@dataclass(frozen=True)
class CoveredCallDecision:
    symbol: str
    as_of_date: str
    decision: CallDecision
    decision_reason: str
    entry_quality: int
    roll_safety: str
    breakout_risk: str
    active_calls: int
    max_active_calls: int
    min_safe_strike: float | None = None
    minimum_atr_distance: float | None = None
    recommended_expiration: str | None = None
    recommended_dte: int | None = None
    recommended_strike: float | None = None
    recommended_delta: float | None = None
    recommended_moneyness: float | None = None
    recommended_atr_distance: float | None = None
    expected_credit: float | None = None
    safer_alternative: dict[str, Any] | None = None
    riskier_alternative: dict[str, Any] | None = None
    no_sell_reasons: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    data_timestamp: str | None = None
    event_risk: str = "UNKNOWN"
    profile_status: str = ProfileStatus.VALIDATED.value
    spot: float | None = None
    sma20: float | None = None
    atr: float | None = None
    extension20_atr: float | None = None
    momentum_state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.update({"module": "pcs.research.covered_call_decision", "version": "1.0",
                    "data_source": "PCS_CANONICAL_DATA_OR_CALLER_PIT_SNAPSHOT",
                    "calculation_version": "covered_call_decision_v1",
                    "request_id": f"{self.symbol}:{self.as_of_date}",
                    "run_id": f"covered-call:{self.symbol}:{self.as_of_date}",
                    "data_timestamp": self.data_timestamp or self.as_of_date})
        out["decision"] = self.decision.value
        out["no_sell_reasons"] = list(self.no_sell_reasons)
        out["reason_codes"] = list(self.reason_codes)
        return out


def build_pit_entry_features(daily: Any, *, as_of_date: str | date) -> dict[str, Any]:
    """Build the small, explainable entry feature set from daily OHLCV.

    Only rows on or before ``as_of_date`` are used. The caller remains
    responsible for canonical routing and PIT provenance.
    """
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame[frame["date"] <= pd.Timestamp(as_of_date).normalize()].sort_values("date")
    if len(frame) < 20:
        return {"status": "DATA_INSUFFICIENT", "reason_codes": ["INSUFFICIENT_DAILY_HISTORY"]}
    close = frame["close"].astype(float)
    high, low = frame["high"].astype(float), frame["low"].astype(float)
    sma20 = close.rolling(20, min_periods=20).mean().iloc[-1]
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean().iloc[-1]
    ret3 = close.pct_change(3); ret5 = close.pct_change(5)
    previous_3 = ret3.iloc[-2] if len(ret3) > 1 else None
    previous_5 = ret5.iloc[-2] if len(ret5) > 1 else None
    delta_momentum = (ret3.iloc[-1] - previous_3) if previous_3 is not None else 0.0
    acceleration = delta_momentum > 0.001
    breakout = bool(close.iloc[-1] >= close.shift(1).rolling(20, min_periods=20).max().iloc[-1])
    iv_state = "UNKNOWN"
    for iv_column in ("iv_rank", "iv_percentile", "iv_percentile_252"):
        if iv_column in frame.columns and pd.notna(frame[iv_column].iloc[-1]):
            iv_state = classify_iv(frame[iv_column].iloc[-1])
            break
    return {"status": "PIT_SAFE", "date": str(frame.date.iloc[-1].date()),
            "close": float(close.iloc[-1]), "atr": float(atr),
            "sma20": float(sma20), "extension20_atr": float((close.iloc[-1] - sma20) / atr),
            "return_3d": float(ret3.iloc[-1]), "return_5d": float(ret5.iloc[-1]),
            "momentum_state": ("ACCELERATING" if delta_momentum > 0.001 else
                               "DECELERATING" if delta_momentum < -0.001 else "STABLE"),
            "near_recent_high": bool(close.iloc[-1] >= close.tail(20).max() * .98),
            "breakout_state": "BREAKOUT" if breakout else "NONE",
            "iv_state": iv_state,
            "reason_codes": ["PIT_DATE_FILTER_APPLIED", "NO_FUTURE_ROWS_USED"]}


def classify_iv(iv_value: Any, *, low: float = .33, high: float = .66) -> str:
    """Map a PIT IV rank/value to the deliberately coarse IV context."""
    if iv_value is None:
        return "UNKNOWN"
    value = float(iv_value)
    return "LOW" if value < low else "HIGH" if value >= high else "NORMAL"


def diagnose_unified_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize a small predeclared decision surface from unified rows.

    Rows with missing features remain in ``UNKNOWN`` buckets; no missing value
    is interpreted as a pass or fail. This is descriptive and does not tune a
    production threshold.
    """
    rows = list(rows)
    def bucket(value: Any, edges: tuple[float, ...], labels: tuple[str, ...]) -> str:
        if value is None:
            return "UNKNOWN"
        x = float(value)
        for i, edge in enumerate(edges):
            if x < edge:
                return labels[i]
        return labels[-1]
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups["extension20_atr=" + bucket(row.get("extension20_atr"), (.5, 1, 1.5, 2),
                                             ("<0.5", ".5-1", "1-1.5", "1.5-2", ">2"))].append(row)
        groups["momentum=" + str(row.get("momentum_state", "UNKNOWN")).upper()].append(row)
        groups["breakout=" + str(row.get("breakout_state", "UNKNOWN")).upper()].append(row)
        groups["iv=" + str(row.get("iv_state", "UNKNOWN")).upper()].append(row)
        groups["qqq=" + str(row.get("qqq_state", "UNKNOWN")).upper()].append(row)
    def metrics(items: list[Mapping[str, Any]]) -> dict[str, Any]:
        pnl = [float(x["combined_pnl"]) for x in items if x.get("combined_pnl") is not None]
        conflicts = sum(x.get("status") == "HARD_CONSTRAINT_CONFLICT" or
                        x.get("exit_state") == "HARD_CONSTRAINT_CONFLICT" for x in items)
        rolls = [float(x.get("roll_count", 0) or 0) for x in items]
        durations = [float(x["holding_days"]) for x in items if x.get("holding_days") is not None]
        years = [str(x.get("entry_date", x.get("date", "")))[:4] for x in items
                 if x.get("entry_date", x.get("date")) is not None]
        year_count = len(set(y for y in years if y.isdigit()))
        option_income = [float(x["call_premium"]) for x in items if x.get("call_premium") is not None]
        itm = [x for x in items if x.get("itm_roll") is not None]
        profitable = [x for x in items if x.get("exit_state") is not None]
        return {"episodes": len(items), "completed_pnl": sum(pnl),
                "calls_per_year": len(items) / year_count if year_count else None,
                "annual_option_income": sum(option_income) / year_count if year_count and option_income else None,
                "itm_roll_rate": (sum(bool(x.get("itm_roll")) for x in itm) / len(itm) if itm else None),
                "profitable_close_rate": (sum(float(x.get("combined_pnl", 0) or 0) > 0 for x in profitable) / len(profitable) if profitable else None),
                "capacity_rejection_rate": (sum(bool(x.get("capacity_rejected")) for x in items) / len(items) if items else None),
                "conflict_rate": conflicts / len(items) if items else None,
                "average_rolls": sum(rolls) / len(rolls) if rolls else None,
                "average_duration": sum(durations) / len(durations) if durations else None}
    return {"module": "pcs.research.covered_call_decision_diagnostics", "version": "1.0",
            "data_source": "PCS_CANONICAL_DATA", "unified_lifecycle_only": True,
            "regions": {key: metrics(value) for key, value in sorted(groups.items())},
            "reason_codes": ["UNIFIED_LIFECYCLE_ROWS_ONLY", "PIT_FEATURES_IF_AVAILABLE",
                             "UNKNOWN_NOT_COERCED", "DESCRIPTIVE_NO_THRESHOLD_TUNING"]}


def evaluate_active_call(episode: CoveredCallEpisode, *, as_of_date: str | date,
                         underlying_price: float, current_quote: CoveredCallContract,
                         roll_quotes: Iterable[CoveredCallContract] = (),
                         mandatory_roll_days: int = 5,
                         profit_capture: float = .60) -> dict[str, Any]:
    """Return a deterministic HOLD/ROLL/CLOSE management decision."""
    if episode.closed or episode.conflicted:
        raise ValueError("EPISODE_NOT_ACTIVE")
    dte = (_date(current_quote.expiration) - _date(as_of_date)).days
    pnl = episode.episode_pnl_if_closed_today(underlying_price, current_quote.ask)
    captured = (episode.cumulative_premium_received - episode.cumulative_buyback_cost -
                current_quote.ask * episode.shares)
    if pnl > 0 and captured >= episode.cumulative_premium_received * float(profit_capture):
        return {"action": PositionDecision.CLOSE.value, "episode_id": episode.episode_id,
                "episode_pnl_if_closed_today": pnl, "reason_codes": ["EPISODE_PROFIT_POSITIVE",
                "PROFIT_CAPTURE_REACHED", "WHOLE_EPISODE_ACCOUNTING"]}
    if dte <= int(mandatory_roll_days):
        selected = CoveredCallRollSelector().select(current_quote, as_of_date,
                                                     underlying_price, roll_quotes)
        if selected is not None:
            new, credit = selected
            return {"action": PositionDecision.ROLL.value, "episode_id": episode.episode_id,
                    "net_roll_credit": credit, "recommended_expiration": new.expiration,
                    "recommended_strike": new.strike,
                    "reason_codes": ["MANDATORY_ROLL_WINDOW", "NON_NEGATIVE_CREDIT_ROLL"]}
    return {"action": PositionDecision.HOLD.value, "episode_id": episode.episode_id,
            "dte": dte, "episode_pnl_if_closed_today": pnl,
            "reason_codes": ["NO_CLOSE_CONDITION", "NO_LEGAL_ROLL_OR_ROLL_NOT_REQUIRED"]}


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _contract_view(c: CoveredCallContract, spot: float, atr: float | None) -> dict[str, Any]:
    dte = (_date(c.expiration) - _date(c.quote_date)).days if c.dte is None else int(c.dte)
    return {"expiration": c.expiration, "dte": dte, "strike": c.strike,
            "delta": c.delta, "moneyness": c.strike / spot - 1.0,
            "atr_distance": ((c.strike - spot) / atr if atr and atr > 0 else None),
            "expected_credit": c.bid * 100, "liquidity_status": "PASS"}


def evaluate_covered_call(symbol: str, as_of_date: str | date, *,
                          stock: Mapping[str, Any] | None = None, market: Mapping[str, Any] | None = None,
                          quotes: Iterable[CoveredCallContract] | None = None,
                          data_access: Any | None = None,
                          active_calls: int = 0, max_active_calls: int = 3,
                          event_context: Mapping[str, Any] | None = None,
                          earnings_window_days: int = 7,
                          profile: CoveredCallProfile | None = None,
                          active_episode: CoveredCallEpisode | None = None,
                          current_quote: CoveredCallContract | None = None,
                          roll_quotes: Iterable[CoveredCallContract] = (),
                          preferred_moneyness: float = 0.20,
                          minimum_atr_distance: float = 3.0,
                          preferred_dte: int = 43) -> dict[str, Any]:
    """Evaluate one post-close, PIT-safe call-sale decision.

    All inputs are decision-date snapshots. No future quote, outcome, or P&L is
    read. The function is ticker-agnostic; ticker routing belongs to callers.
    """
    symbol = str(symbol).upper(); day = str(as_of_date)[:10]
    profile = profile or resolve_covered_call_profile(symbol)
    if profile.status is not ProfileStatus.VALIDATED:
        return CoveredCallDecision(symbol, day, CallDecision.WAIT,
            "ticker covered-call profile is not validated", 0, "UNKNOWN", "UNKNOWN",
            active_calls, max_active_calls,
            no_sell_reasons=("PROFILE_NOT_VALIDATED",),
            reason_codes=("PROFILE_GATE", "FAIL_CLOSED"),
            profile_status=profile.status.value).to_dict()
    preferred_moneyness = profile.min_moneyness if profile.min_moneyness is not None else preferred_moneyness
    minimum_atr_distance = profile.min_atr_distance if profile.min_atr_distance is not None else minimum_atr_distance
    preferred_dte = profile.preferred_dte if profile.preferred_dte is not None else preferred_dte
    if active_episode is not None:
        if current_quote is None:
            raise ValueError("CURRENT_QUOTE_REQUIRED_FOR_ACTIVE_CALL")
        underlying = float((stock or {}).get("close", current_quote.underlying_price or 0))
        management = evaluate_active_call(active_episode, as_of_date=day,
                                          underlying_price=underlying,
                                          current_quote=current_quote,
                                          roll_quotes=roll_quotes)
        management.update({"symbol": symbol, "as_of_date": day,
                           "decision": management["action"],
                           "event_risk": "UNKNOWN", "profile_status": profile.status.value,
                           "current_strike": current_quote.strike,
                           "current_expiration": current_quote.expiration,
                           "current_dte": (_date(current_quote.expiration) - _date(day)).days,
                           "buyback_cost": current_quote.ask * 100,
                           "reason_codes": management.get("reason_codes", [])})
        if management["action"] == PositionDecision.ROLL.value:
            new = next((q for q in roll_quotes if q.expiration == management.get("recommended_expiration")
                        and q.strike == management.get("recommended_strike")), None)
            if new is not None:
                management.update({"new_strike": new.strike, "new_expiration": new.expiration,
                                   "new_dte": (_date(new.expiration) - _date(day)).days,
                                   "new_credit": new.bid * 100,
                                   "net_roll_credit": management["net_roll_credit"]})
        management.update({"module": "pcs.research.covered_call_decision", "version": "1.0",
                           "data_source": "PCS_CANONICAL_DATA_OR_CALLER_PIT_SNAPSHOT",
                           "calculation_version": "covered_call_decision_v1",
                           "request_id": f"{symbol}:{day}", "run_id": f"covered-call:{symbol}:{day}"})
        return management
    market = market or {}
    event_context = event_context or {}
    if market.get("market_state") is None:
        return CoveredCallDecision(symbol, day, CallDecision.WAIT,
            "market state is unknown", 0, "UNKNOWN", "UNKNOWN", active_calls,
            max_active_calls, no_sell_reasons=("MARKET_STATE_UNKNOWN",),
            reason_codes=("MARKET_CONTEXT_REQUIRED", "FAIL_CLOSED")).to_dict()
    if event_context.get("earnings_status") not in {"KNOWN", "NO_EVENT"}:
        return CoveredCallDecision(symbol, day, CallDecision.WAIT,
            "earnings context is unknown", 0, "UNKNOWN", "UNKNOWN", active_calls,
            max_active_calls, no_sell_reasons=("EARNINGS_UNKNOWN",),
            reason_codes=("EVENT_CONTEXT_REQUIRED", "FAIL_CLOSED")).to_dict()
    earnings_date = (_date(event_context["earnings_date"])
                     if event_context.get("earnings_date") else None)
    days_to_earnings = ((earnings_date - _date(day)).days if earnings_date else
                        event_context.get("days_to_earnings"))
    if stock is None:
        if data_access is None:
            raise ValueError("PIT_STOCK_FEATURES_OR_DATA_ACCESS_REQUIRED")
        prices = data_access.read_prices(symbol, end_date=day)
        stock = build_pit_entry_features(prices, as_of_date=day)
        if stock.get("status") == "DATA_INSUFFICIENT":
            return CoveredCallDecision(symbol, day, CallDecision.WAIT,
                "insufficient canonical daily history for PIT indicators", 0, "UNKNOWN", "UNKNOWN",
                active_calls, max_active_calls, no_sell_reasons=("INSUFFICIENT_DAILY_HISTORY",),
                reason_codes=("CANONICAL_DAILY_DATA_INSUFFICIENT",)).to_dict()
        latest = _date(stock["date"])
        requested = _date(day)
        if latest < requested and (requested - latest).days > 3:
            return CoveredCallDecision(symbol, day, CallDecision.WAIT,
                "canonical market data is stale for the requested decision date", 0,
                "UNKNOWN", "UNKNOWN", active_calls, max_active_calls,
                no_sell_reasons=("CANONICAL_DATA_NOT_CURRENT",),
                reason_codes=("DATA_FRESHNESS_GATE", "NO_FUTURE_DATA_USED"),
                data_timestamp=str(stock["date"])).to_dict()
    if quotes is None:
        if data_access is None:
            quotes = ()
        else:
            try:
                frame = data_access.read_option_chain(symbol, day)
            except (ValueError, FileNotFoundError) as exc:
                return CoveredCallDecision(symbol, day, CallDecision.NO_SELL,
                    "canonical option chain is unavailable for the requested date", 0,
                    "UNKNOWN", "UNKNOWN", active_calls, max_active_calls,
                    no_sell_reasons=("OPTIONS_DATA_UNAVAILABLE",),
                    reason_codes=("CANONICAL_OPTIONS_COVERAGE_GATE", "FAIL_CLOSED"),
                    data_timestamp=str(stock.get("date", day))).to_dict()
            quotes = [CoveredCallContract(symbol.upper(), str(day), str(r.expiration_date), float(r.strike),
                                           float(r.bid), float(r.ask),
                                           float(r.delta) if getattr(r, "delta", None) is not None else None,
                                           int(r.open_interest) if getattr(r, "open_interest", None) is not None else None,
                                           int(r.volume) if getattr(r, "volume", None) is not None else None,
                                           dte=(_date(r.expiration_date) - _date(day)).days)
                       for r in frame[frame.call_put.astype(str).str.lower().isin({"c", "call"})].itertuples()]
    if active_calls >= max_active_calls:
        return CoveredCallDecision(symbol, day, CallDecision.NO_SELL,
            "all short-call capacity is occupied", 0, "LOW", "UNKNOWN", active_calls,
            max_active_calls, no_sell_reasons=("MAX_CALL_CAPACITY_REACHED",),
            reason_codes=("CAPACITY_HARD_STOP",)).to_dict()
    if days_to_earnings is not None and 0 <= int(days_to_earnings) <= int(earnings_window_days):
        return CoveredCallDecision(symbol, day, CallDecision.NO_SELL,
            "earnings are inside the event-risk window", 0, "LOW", "HIGH",
            active_calls, max_active_calls, no_sell_reasons=("EARNINGS_SOON",),
            reason_codes=("EVENT_RISK_HARD_STOP",), event_risk="HIGH").to_dict()
    required = ("close", "atr", "extension20_atr", "momentum_state")
    missing = tuple(k for k in required if stock.get(k) is None)
    if missing:
        return CoveredCallDecision(symbol, day, CallDecision.WAIT,
            "PIT-safe entry features are incomplete", 0, "UNKNOWN", "UNKNOWN",
            active_calls, max_active_calls, no_sell_reasons=missing,
            reason_codes=("MISSING_PIT_FEATURE",)).to_dict()
    spot = float(stock["close"]); atr = float(stock["atr"])
    extension = float(stock["extension20_atr"])
    momentum = str(stock["momentum_state"]).upper()
    breakout = str(stock.get("breakout_state", "NONE")).upper()
    qqq = str(market.get("qqq_state", "QQQ_NEUTRAL")).upper()
    iv_state = str(stock.get("iv_state", "UNKNOWN")).upper()
    dangerous = momentum == "ACCELERATING" and breakout in {"BREAKOUT", "ACCELERATING"}
    developing = momentum == "ACCELERATING" or breakout in {"BREAKOUT", "ACCELERATING"} or qqq == "QQQ_STRONG"
    near_high = bool(stock.get("near_recent_high", extension >= 1.0))
    quality = max(0, min(100, 45 + (20 if extension >= 1 else 0) +
                          (15 if momentum == "DECELERATING" else -15 if momentum == "ACCELERATING" else 0) +
                          (10 if near_high else 0) - (20 if dangerous else 0)))
    reason_codes = ["PIT_SAFE_FEATURES", "CAPACITY_AVAILABLE"]
    if dangerous:
        return CoveredCallDecision(symbol, day, CallDecision.NO_SELL,
            "breakout acceleration and rising momentum create elevated roll risk", quality,
            "LOW", "HIGH", active_calls, max_active_calls,
            no_sell_reasons=("BREAKOUT_ACCELERATION", "MOMENTUM_ACCELERATING"),
            reason_codes=tuple(reason_codes + ["BREAKOUT_RISK_GATE"])).to_dict()
    if developing or extension < 0.5:
        return CoveredCallDecision(symbol, day, CallDecision.WAIT,
            "extension or momentum state is not yet a repeatable sell setup", quality,
            "MEDIUM", "MEDIUM" if developing else "LOW", active_calls, max_active_calls,
            reason_codes=tuple(reason_codes + ["SETUP_NOT_MATURE"])).to_dict()
    candidates = []
    for c in quotes:
        if c.symbol.upper() != symbol or _date(c.expiration) <= _date(day):
            continue
        dte = (_date(c.expiration) - _date(day)).days
        atr_distance = ((c.strike - spot) / atr) if atr > 0 else 0.0
        if (dte < 30 or dte > 60 or
                (earnings_date is not None and _date(day) < earnings_date <= _date(c.expiration)) or
                c.strike < spot * (1.0 + preferred_moneyness) or
                atr_distance < minimum_atr_distance):
            continue
        if (c.bid <= 0 or c.ask < c.bid or c.spread_pct > 0.20 or
                c.open_interest is None or c.volume is None or
                c.open_interest < 100 or c.volume < 1):
            continue
        if c.delta is not None and c.delta > 0.30:
            continue
        candidates.append(c)
    candidates.sort(key=lambda c: (-c.bid, abs((c.dte or (_date(c.expiration)-_date(day)).days) - preferred_dte), c.spread_pct))
    if not candidates:
        return CoveredCallDecision(symbol, day, CallDecision.NO_SELL,
            "no safe liquid canonical call meets the frozen distance and DTE constraints", quality,
            "LOW", "LOW", active_calls, max_active_calls,
            min_safe_strike=spot * (1.0 + preferred_moneyness),
            minimum_atr_distance=minimum_atr_distance,
            no_sell_reasons=(("NO_SAFE_CANONICAL_OPTION", "EXPIRATION_CROSSES_EARNINGS")
                             if earnings_date else ("NO_SAFE_CANONICAL_OPTION",)),
            reason_codes=tuple(reason_codes + ["CONTRACT_SELECTION_EMPTY"]),
            data_timestamp=str(stock.get("date", day))).to_dict()
    chosen = candidates[0]; view = _contract_view(chosen, spot, atr)
    alternatives = [_contract_view(c, spot, atr) for c in candidates[1:3]]
    chosen_delta = float(chosen.delta) if chosen.delta is not None else 0.30
    chosen_distance = float(view["moneyness"])
    chosen_atr = view["atr_distance"]
    chosen_dte = int(view["dte"])
    safety_points = (2 if chosen_delta <= .20 else 1 if chosen_delta <= .25 else 0)
    safety_points += 1 if chosen_distance >= .20 else 0
    safety_points += 1 if chosen_atr is not None and chosen_atr >= 3 else 0
    safety_points -= 1 if qqq == "QQQ_STRONG" else 0
    safety_points += 1 if qqq == "QQQ_WEAKENING" else 0
    safety_points += 1 if iv_state == "HIGH" else 0
    safety_points -= 1 if momentum == "ACCELERATING" else 0
    safety_points += 1 if 35 <= chosen_dte <= 50 else -1 if chosen_dte < 35 or chosen_dte > 55 else 0
    roll_safety = "HIGH" if safety_points >= 3 else "MEDIUM" if safety_points >= 1 else "LOW"
    return CoveredCallDecision(symbol, day, CallDecision.SELL,
        "extension is meaningful, momentum is not accelerating, and a safe liquid call is available",
        quality, roll_safety, "LOW", active_calls, max_active_calls,
        min_safe_strike=spot * (1.0 + preferred_moneyness),
        minimum_atr_distance=minimum_atr_distance,
        recommended_expiration=view["expiration"], recommended_dte=view["dte"],
        recommended_strike=view["strike"], recommended_delta=view["delta"],
        recommended_moneyness=view["moneyness"], recommended_atr_distance=view["atr_distance"],
        expected_credit=view["expected_credit"],
        # Candidates are ordered by highest legal strike first; lower strikes
        # increase assignment/roll pressure and are therefore riskier.
        safer_alternative=None,
        riskier_alternative=alternatives[0] if alternatives else None,
        reason_codes=tuple(reason_codes + ["SAFE_REGION_FILTER", "LIQUIDITY_PASS"]),
        data_timestamp=str(stock.get("date", day)), spot=spot, sma20=stock.get("sma20"),
        atr=atr, extension20_atr=extension, momentum_state=momentum).to_dict()
