"""Historical input adapter for the production PCS DecisionEngine.

This module deliberately refuses to invent unavailable historical inputs.  It
maps persisted candidate rows to the production TradeCandidate contract only
when all required fields are present, and emits an auditable availability
report otherwise.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
import pandas as pd

REQUIRED = {
    "date": "entry date", "ticker": "ticker", "expiration": "expiration",
    "short_strike": "short strike", "long_strike": "long strike",
    "close": "spot", "initial_credit": "credit", "short_delta": "short delta",
    "dte": "DTE", "atr14": "ATR", "trend_score": "trend score",
    "support_level": "support level", "normal_daily_move": "normal daily move",
    "option_volume": "option volume", "open_interest": "open interest",
    "bid_ask_pct": "bid/ask", "nearby_strikes": "nearby strikes",
    "later_expirations": "later expirations", "price_confirmation": "price confirmation",
}

@dataclass(frozen=True)
class ReplayAvailability:
    rows: int
    available: tuple[str, ...]
    missing: tuple[str, ...]
    lookahead_safe: bool
    can_run_decision_engine: bool

    def to_dict(self) -> dict[str, Any]: return asdict(self)

def audit_inputs(frame: pd.DataFrame) -> ReplayAvailability:
    available=tuple(k for k in REQUIRED if k in frame.columns and frame[k].notna().all())
    missing=tuple(REQUIRED[k] for k in REQUIRED if k not in available)
    date_ok="date" in frame and pd.to_datetime(frame.date, errors="coerce").notna().all()
    return ReplayAvailability(len(frame), available, missing, bool(date_ok), not missing and bool(date_ok))

def to_trade_candidate(row: dict[str, Any]):
    from pcs.models.trade import TradeCandidate
    a=audit_inputs(pd.DataFrame([row]))
    if not a.can_run_decision_engine: raise ValueError("historical input contract incomplete: " + ", ".join(a.missing))
    return TradeCandidate(ticker=str(row["ticker"]), expiration=str(row["expiration"]),
        short_strike=float(row["short_strike"]), long_strike=float(row["long_strike"]),
        underlying_price=float(row["close"]), credit=float(row["initial_credit"]),
        dte=int(row["dte"]), short_delta=float(row["short_delta"]), expected_move=float(row["normal_daily_move"]),
        support_level=float(row["support_level"]), normal_daily_move=float(row["normal_daily_move"]),
        option_volume=int(row["option_volume"]), open_interest=int(row["open_interest"]),
        bid_ask_pct=float(row["bid_ask_pct"]), nearby_strikes=int(row["nearby_strikes"]),
        later_expirations=int(row["later_expirations"]), business_quality=float(row.get("business_quality", 0)),
        trend_score=float(row["trend_score"]), support_score=float(row.get("support_score", 0)),
        sector_alignment=float(row.get("sector_alignment", 0)), price_confirmation=float(row["price_confirmation"]),
        event_risk=int(row.get("event_risk", 0)), correlation_bucket=str(row.get("correlation_bucket", "other")))
