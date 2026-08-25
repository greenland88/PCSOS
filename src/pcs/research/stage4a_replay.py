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
from pcs.entry.contract_v2 import ENTRY_CONTRACT_V2
from pcs.entry.support_contract import SupportState, support_contract_valid

REQUIRED_V1 = {
    "date": "entry date", "ticker": "ticker", "expiration": "expiration",
    "short_strike": "short strike", "long_strike": "long strike",
    "close": "spot", "initial_credit": "credit", "short_delta": "short delta",
    "dte": "DTE", "atr14": "ATR", "trend_score": "trend score",
    "support_level": "support level", "normal_daily_move": "normal daily move",
    "option_volume": "option volume", "open_interest": "open interest",
    "bid_ask_pct": "bid/ask", "nearby_strikes": "nearby strikes",
    "later_expirations": "later expirations", "price_confirmation": "price confirmation",
}

REQUIRED = {k: v for k, v in REQUIRED_V1.items() if k != "normal_daily_move"}
REQUIRED["expected_move_1d"] = "expected move 1d"

@dataclass(frozen=True)
class ReplayAvailability:
    rows: int
    available: tuple[str, ...]
    missing: tuple[str, ...]
    lookahead_safe: bool
    can_run_decision_engine: bool
    contract_complete: bool = False
    entry_eligible: bool = False

    def to_dict(self) -> dict[str, Any]: return asdict(self)

def audit_inputs(frame: pd.DataFrame) -> ReplayAvailability:
    if len(frame) == 0:
        return ReplayAvailability(0, (), tuple(REQUIRED.values()), False, False, False, False)
    available=tuple(k for k in REQUIRED if k in frame.columns and frame[k].notna().all())
    missing_list=[REQUIRED[k] for k in REQUIRED if k not in available]
    version_ok = "entry_contract_version" in frame and frame.entry_contract_version.eq(ENTRY_CONTRACT_V2).all()
    date_ok="date" in frame and pd.to_datetime(frame.date, errors="coerce").notna().all()
    if not version_ok:
        missing_list.append("ENTRY_CONTRACT_V2 version")
    support_columns = {"support_state", "support_level", "support_reason", "support_producer_version", "support_asof", "support_provenance"}
    support_metadata_ok = support_columns.issubset(frame.columns)
    support_values_ok = support_metadata_ok and all(support_contract_valid(row) for row in frame.to_dict("records"))
    if support_values_ok and frame.support_state.eq(SupportState.NO_SUPPORT).any():
        missing_list = [value for value in missing_list if value != "support level"]
    if not support_values_ok:
        missing_list.append("support state/provenance")
    missing = tuple(dict.fromkeys(missing_list))
    complete = not missing and bool(date_ok and version_ok)
    entry_eligible = bool(complete and frame.support_state.eq(SupportState.SUPPORT_FOUND).all())
    return ReplayAvailability(len(frame), available, missing, bool(date_ok and version_ok), entry_eligible, bool(complete), entry_eligible)

def to_trade_candidate(row: dict[str, Any]):
    from pcs.models.trade import TradeCandidate
    a=audit_inputs(pd.DataFrame([row]))
    if not a.can_run_decision_engine: raise ValueError("historical input contract incomplete: " + ", ".join(a.missing))
    return TradeCandidate(ticker=str(row["ticker"]), expiration=str(row["expiration"]),
        short_strike=float(row["short_strike"]), long_strike=float(row["long_strike"]),
        underlying_price=float(row["close"]), credit=float(row["initial_credit"]),
        dte=int(row["dte"]), short_delta=float(row["short_delta"]), expected_move=float(row["expected_move_1d"]),
        expected_move_1d=float(row["expected_move_1d"]), support_level=float(row["support_level"]),
        option_volume=int(row["option_volume"]), open_interest=int(row["open_interest"]),
        bid_ask_pct=float(row["bid_ask_pct"]), nearby_strikes=int(row["nearby_strikes"]),
        later_expirations=int(row["later_expirations"]), business_quality=float(row.get("business_quality", 0)),
        trend_score=float(row["trend_score"]), support_score=float(row.get("support_score", 0)),
        sector_alignment=float(row.get("sector_alignment", 0)), price_confirmation=float(row["price_confirmation"]),
        atr=float(row["atr14"]), bid=float(row["short_bid"]) if pd.notna(row.get("short_bid")) else None,
        ask=float(row["short_ask"]) if pd.notna(row.get("short_ask")) else None,
        long_bid=float(row["long_bid"]) if pd.notna(row.get("long_bid")) else None,
        long_ask=float(row["long_ask"]) if pd.notna(row.get("long_ask")) else None,
        long_option_volume=int(row["long_volume"]) if pd.notna(row.get("long_volume")) else None,
        long_open_interest=int(row["long_open_interest"]) if pd.notna(row.get("long_open_interest")) else None,
        event_risk=int(row.get("event_risk", 0)), correlation_bucket=str(row.get("correlation_bucket", "other")))
