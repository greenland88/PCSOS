"""Single freshness boundary for production decisions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import pandas as pd

from .access import PCSDataAccess
from .control_plane import MarketDataControlPlane


@dataclass(frozen=True)
class LiveMarketState:
    status: str
    symbol: str
    decision_as_of: str
    required_session: str | None
    daily: pd.DataFrame
    options: pd.DataFrame
    reason_codes: tuple[str, ...] = ()
    recovery: dict[str, Any] | None = None


def require_live_market_state(symbol: str, decision_as_of: str, *, data_access: PCSDataAccess | None = None,
                              min_dte: int = 7, max_dte: int = 45, option_type: str = "put",
                              required_fields: tuple[str, ...] = ("expiration_date", "strike", "bid", "ask", "delta", "open_interest")) -> LiveMarketState:
    if not decision_as_of:
        raise ValueError("DECISION_AS_OF_REQUIRED")
    access = data_access or PCSDataAccess.canonical()
    symbol = str(symbol).strip().upper()
    as_of = pd.Timestamp(decision_as_of).normalize()
    # Date-only EOD requests made on a trading day refer to the prior
    # completed session. Holiday-specific callers should pass that completed
    # session explicitly; this avoids demanding an intraday bar from EOD data.
    required_session = as_of - pd.offsets.BDay(1)

    def gates() -> tuple[tuple[str, ...], pd.DataFrame, pd.DataFrame, str | None]:
        # Production date-only decisions are post-session decisions.  The
        # requested session is therefore the decision date; a caller that
        # needs a prior session must pass that prior completed date explicitly.
        required = required_session
        daily = access.read_prices(symbol, end_date=required)
        if daily.empty:
            return ("DAILY_FRESHNESS_GATE_FAILED",), daily, pd.DataFrame(), None
        session = pd.to_datetime(daily["date"]).dt.normalize().max()
        reasons: list[str] = []
        if session < required:
            reasons.extend(("DAILY_FRESHNESS_GATE_FAILED", "CANONICAL_DAILY_STALE"))
        if len(daily) < 200:
            reasons.extend(("FEATURE_WARMUP_GATE_FAILED", "DAILY_HISTORY_WARMUP_INSUFFICIENT"))
        options = access.read_quotes(symbol, session.date().isoformat(), session.date().isoformat())
        if options.empty:
            reasons.append("OPTIONS_FRESHNESS_GATE_FAILED")
        else:
            latest = pd.to_datetime(options["trade_date"]).dt.normalize().max()
            if latest < required:
                reasons.extend(("OPTIONS_FRESHNESS_GATE_FAILED", "CANONICAL_OPTIONS_STALE"))
            missing = sorted(set(required_fields) - set(options.columns))
            if missing:
                reasons.extend(("REQUIRED_FIELD_GATE_FAILED", "REQUIRED_OPTION_FIELDS_MISSING"))
            if not options.empty and option_type:
                side = options[options.call_put.astype(str).str.lower().isin({option_type.lower(), option_type[:1].lower()})]
                dte = (pd.to_datetime(side.expiration_date) - pd.to_datetime(side.trade_date)).dt.days
                if not bool(dte.between(min_dte, max_dte).any()):
                    reasons.extend(("OPTION_CHAIN_READINESS_GATE_FAILED", "CURRENT_OPTION_CHAIN_GAP"))
        return tuple(dict.fromkeys(reasons)), daily, options, str(session.date())

    reasons, daily, options, session = gates()
    recovery = None
    if reasons:
        history_start = str((required_session - pd.Timedelta(days=365)).date())
        req = {"symbol": symbol, "datasets": ("daily", "options"), "start": history_start,
               "end": str(as_of.date()), "as_of": str(as_of.date()), "decision_as_of": str(as_of.date()),
               "required_history_rows": 200,
               "option_type": option_type, "min_dte": min_dte, "max_dte": max_dte,
               "required_fields": required_fields, "consumer": "LIVE_DECISION"}
        result = MarketDataControlPlane(access=access).ensure_market_data(req)
        recovery = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        reasons, daily, options, session = gates()
    if reasons:
        # A failed live-data gate is a data state, never a strategy WAIT.
        # Callers must not be able to interpret stale/unavailable quotes as a
        # normal opportunity decision.
        return LiveMarketState("BLOCKED", symbol, str(as_of.date()), session, daily, options, reasons, recovery)
    return LiveMarketState("READY", symbol, str(as_of.date()), session, daily, options, (), recovery)


__all__ = ["LiveMarketState", "require_live_market_state"]
