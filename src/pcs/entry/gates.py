from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class GateResult:
    gate: str
    status: GateStatus
    reason_codes: tuple[str, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)


class EventGate:
    def evaluate(self, candidate, calendar=None) -> GateResult:
        risk = int(getattr(candidate, "event_risk", 0))
        if calendar is None:
            return GateResult("event", GateStatus.PASS, ("EVENT_CALENDAR_UNAVAILABLE_IGNORED",))
        if calendar.empty:
            return GateResult("event", GateStatus.PASS)
        try:
            import pandas as pd
            rows = calendar[(calendar.event_type == "EARNINGS") & ((calendar.symbol == candidate.ticker) | calendar.symbol.isna())]
        except Exception:
            return GateResult("event", GateStatus.PASS, ("EVENT_CALENDAR_NO_TICKER_EVENT",))
        if rows.empty:
            return GateResult("event", GateStatus.PASS, ("EVENT_CALENDAR_NO_TICKER_EVENT",))
        if "event_date_known_at_entry" not in calendar.columns:
            return GateResult("event", GateStatus.FAIL, ("EVENT_CALENDAR_PIT_METADATA_MISSING",))
        known = calendar["event_date_known_at_entry"].astype(str).str.upper()
        if not known.isin({"YES", "TRUE", "1"}).all():
            return GateResult("event", GateStatus.FAIL, ("EVENT_CALENDAR_PIT_METADATA_UNVERIFIED",))
        if risk > 0:
            return GateResult("event", GateStatus.FAIL, ("EVENT_RISK_PRESENT",))
        try:
            import pandas as pd
            entry = pd.Timestamp(getattr(candidate, "entry_date", None))
            expiry = pd.Timestamp(candidate.expiration)
            for date in pd.to_datetime(rows.event_date).dt.normalize():
                # Historical events strictly before entry cannot create either
                # expiration exposure or a new-entry blackout.
                if date < entry:
                    continue
                if entry <= date <= expiry:
                    return GateResult("event", GateStatus.FAIL, ("EVENT_EARNINGS_CROSSING",))
                if date > expiry:
                    continue
                sessions = getattr(candidate, "trading_sessions", None)
                if sessions is None:
                    return GateResult("event", GateStatus.FAIL, ("TRADING_SESSION_CALENDAR_UNAVAILABLE",))
                session_index = pd.DatetimeIndex(pd.to_datetime(sessions)).normalize()
                if entry not in session_index or date not in session_index:
                    return GateResult("event", GateStatus.FAIL, ("TRADING_SESSION_CALENDAR_INVALID",))
                distance = int(session_index.get_loc(date)) - int(session_index.get_loc(entry))
                if 0 <= distance <= 3:
                    return GateResult("event", GateStatus.FAIL, ("EVENT_PRE_EARNINGS_BLACKOUT",))
        except Exception:
            return GateResult("event", GateStatus.FAIL, ("EVENT_CALENDAR_INVALID",))
        return GateResult("event", GateStatus.PASS)


class RegimeGate:
    def __init__(self, rules): self.rules = rules["regime"]
    def evaluate(self, regime, entry_context=None) -> GateResult:
        if str(regime) == "RED": return GateResult("regime", GateStatus.FAIL, ("REGIME_RED",))
        if str(regime) == "YELLOW":
            if entry_context is None: return GateResult("regime", GateStatus.PASS, ("YELLOW_CONDITIONAL",))
            if entry_context.entry_context_state != "READY": return GateResult("regime", GateStatus.FAIL, ("YELLOW_STRUCTURE_FAIL",))
        return GateResult("regime", GateStatus.PASS)


class DTEGate:
    def __init__(self, rules): self.rules = rules["entry"]
    def evaluate(self, candidate) -> GateResult:
        lo = self.rules.get("hard_dte_min")
        hi = self.rules.get("hard_dte_max")
        dte = int(candidate.dte)
        if lo is None or hi is None:
            return GateResult("dte", GateStatus.PASS, ("DTE_HARD_RANGE_NOT_ACTIVATED",), {"dte": dte})
        if lo <= dte <= hi: return GateResult("dte", GateStatus.PASS, diagnostics={"dte": dte})
        return GateResult("dte", GateStatus.FAIL, ("DTE_OUT_OF_HARD_RANGE",), {"dte": dte})


class LiquidityGate:
    def __init__(self, rules): self.rules = rules["liquidity"]
    def evaluate(self, candidate) -> GateResult:
        failures = []
        for value in (candidate.bid, candidate.ask):
            if value is None or value <= 0: failures.append("LIQUIDITY_QUOTE_INVALID")
        if candidate.bid is not None and candidate.ask is not None and candidate.bid > candidate.ask: failures.append("LIQUIDITY_QUOTE_CROSSED")
        if candidate.option_volume < self.rules["min_option_volume"]: failures.append("OPTION_VOLUME_BELOW_MINIMUM")
        if candidate.open_interest < self.rules["min_open_interest"]: failures.append("SHORT_LEG_OPEN_INTEREST_BELOW_MINIMUM")
        if candidate.bid_ask_pct > self.rules["max_bid_ask_pct"]: failures.append("SHORT_LEG_SPREAD_TOO_WIDE")
        if candidate.nearby_strikes < self.rules["min_nearby_strikes"]: failures.append("NEARBY_STRIKES_INSUFFICIENT")
        if candidate.later_expirations < self.rules["min_later_expirations"]: failures.append("EXPIRATIONS_INSUFFICIENT")
        long_volume = getattr(candidate, "long_option_volume", None)
        long_oi = getattr(candidate, "long_open_interest", None)
        if long_volume is None or long_oi is None or getattr(candidate, "long_bid", None) is None or getattr(candidate, "long_ask", None) is None:
            failures.append("LIQUIDITY_LONG_LEG_FAIL")
        else:
            if self.rules.get("long_leg_min_option_volume") is not None and long_volume < self.rules["long_leg_min_option_volume"]: failures.append("LONG_LEG_VOLUME_BELOW_MINIMUM")
            if self.rules.get("long_leg_min_open_interest") is not None and long_oi < self.rules["long_leg_min_open_interest"]: failures.append("LONG_LEG_OPEN_INTEREST_BELOW_MINIMUM")
        return GateResult("liquidity", GateStatus.FAIL if failures else GateStatus.PASS, tuple(failures))


class SafeStrikeGate:
    def __init__(self, rules, price_basis_service=None):
        self.rules = rules["entry"]
        self.price_basis_service = price_basis_service
    def evaluate(self, candidate) -> GateResult:
        atr = getattr(candidate, "atr", None)
        if atr is None: return GateResult("safe_strike", GateStatus.FAIL, ("SAFE_STRIKE_ATR_UNAVAILABLE",))
        comparison = getattr(candidate, "comparison_short_strike", None)
        if comparison is None and self.price_basis_service is not None:
            try:
                comparison = self.price_basis_service.to_comparison_strike(candidate.ticker, candidate.entry_date, candidate.short_strike)
            except Exception as exc:
                return GateResult("safe_strike", GateStatus.FAIL, (str(exc),))
        comparison = candidate.short_strike if comparison is None else comparison
        buffer = (candidate.underlying_price - comparison) / float(atr)
        required = self.rules["safe_strike_atr"]
        return GateResult("safe_strike", GateStatus.PASS if buffer >= required else GateStatus.FAIL,
                          () if buffer >= required else ("SAFE_STRIKE_BUFFER_INSUFFICIENT",),
                          {"buffer_atr": buffer, "required_atr": required, "raw_short_strike": candidate.short_strike,
                           "comparison_short_strike": comparison, "price_basis": "ANALYTIC_ADJUSTED"})


class CreditEfficiencyGate:
    def __init__(self, rules): self.rules = rules["entry"]
    def evaluate(self, candidate) -> GateResult:
        width = candidate.short_strike - candidate.long_strike
        ratio = candidate.credit / width if width > 0 else 0.0
        minimum = self.rules.get("min_credit_width_ratio")
        if minimum is None: return GateResult("credit_efficiency", GateStatus.PASS, ("CREDIT_FLOOR_NOT_ACTIVATED",), {"ratio": ratio})
        return GateResult("credit_efficiency", GateStatus.PASS if ratio >= minimum else GateStatus.FAIL,
                          () if ratio >= minimum else ("CREDIT_EFFICIENCY_BELOW_MINIMUM",), {"ratio": ratio})


class PortfolioRiskGate:
    def __init__(self, rules): self.rules = rules["portfolio"]
    def evaluate(self, snapshot) -> GateResult:
        if snapshot.planned_loss >= self.rules["max_planned_risk"]: return GateResult("portfolio_risk", GateStatus.FAIL, ("PORTFOLIO_PLANNED_LOSS_LIMIT",))
        return GateResult("portfolio_risk", GateStatus.PASS)


class HardGatePipeline:
    def __init__(self, rules):
        self.regime = RegimeGate(rules); self.event = EventGate(); self.dte = DTEGate(rules); self.liquidity = LiquidityGate(rules)
        self.safe_strike = SafeStrikeGate(rules); self.credit = CreditEfficiencyGate(rules); self.portfolio = PortfolioRiskGate(rules)
    def evaluate(self, candidate, portfolio_snapshot, regime=None, event_calendar=None, entry_context=None) -> tuple[GateResult, ...]:
        results = ([] if regime is None else [self.regime.evaluate(regime, entry_context)]) + [self.event.evaluate(candidate, event_calendar), self.safe_strike.evaluate(candidate), self.dte.evaluate(candidate), self.liquidity.evaluate(candidate), self.credit.evaluate(candidate), self.portfolio.evaluate(portfolio_snapshot)]
        if entry_context is not None:
            status = GateStatus.PASS if entry_context.entry_context_state == "READY" else GateStatus.FAIL
            results.extend([GateResult("trend", status, tuple(entry_context.reasons)), GateResult("pullback", status, tuple(entry_context.reasons))])
        else:
            results.extend([GateResult("trend", GateStatus.FAIL, ("PREDICTABILITY_CONTEXT_UNAVAILABLE",)), GateResult("pullback", GateStatus.FAIL, ("ENTRY_CONTEXT_UNAVAILABLE",))])
        return tuple(results)


def build_production_entry_context(candidate):
    """Construct the deterministic entry context from candidate-derived inputs."""
    from pcs.entry.context import build_entry_context
    from pcs.entry.pullback_gate import evaluate_pullback_gate
    from pcs.entry.trend_gate import evaluate_trend_gate
    trend_score = getattr(candidate, "trend_score_result", None)
    interpretation = getattr(candidate, "trend_interpretation", None)
    snapshot = getattr(candidate, "trend_snapshot", None)
    if trend_score is None or interpretation is None or snapshot is None:
        return None
    trend = evaluate_trend_gate(trend_score, interpretation, snapshot)
    pullback = evaluate_pullback_gate(trend, snapshot, interpretation)
    from pcs.entry.strike_gate import evaluate_short_strike
    strike = evaluate_short_strike(candidate.short_strike, snapshot, interpretation, trend, pullback)
    return build_entry_context(trend, pullback, strike)
