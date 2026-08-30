"""Production-only, read-only NVDA covered-call decision boundary.

This module does not import or invoke research runners.  Broker/account and
market adapters are injected so unavailable live state fails closed.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date
from typing import Any, Mapping
from enum import StrEnum
from pcs.research.covered_call_profiles import resolve_covered_call_profile, ProfileStatus


@dataclass(frozen=True)
class DecisionPacket:
    symbol: str
    as_of: str
    action: str
    reason_codes: tuple[str, ...]
    profile: Mapping[str, Any]
    shares: int
    open_calls: int
    available_capacity: int
    selected_contract: Mapping[str, Any] | None = None
    gate_results: Mapping[str, Any] | None = None
    decision_status: str = "EVALUATED"

    def to_dict(self):
        out = asdict(self); out["reason_codes"] = list(self.reason_codes)
        return {"module": "pcs.covered_call_production", "version": "1.1",
                "data_source": "INJECTED_LIVE_PROVIDER", **out}


class RequestDataMode(StrEnum):
    PRODUCTION_LIVE = "PRODUCTION_LIVE"
    RESEARCH_PIT = "RESEARCH_PIT"


def _value(row, key, default=None):
    return row.get(key, default) if isinstance(row, Mapping) else getattr(row, key, default)


def decide_nvda_call_today(provider: Any, *, as_of: str | date,
                           mode: RequestDataMode = RequestDataMode.PRODUCTION_LIVE) -> dict[str, Any]:
    """Answer whether one NVDA call can be sold today from live snapshots."""
    day = str(as_of)[:10]; profile = resolve_covered_call_profile("NVDA")
    try:
        mode = RequestDataMode(mode)
    except ValueError:
        mode = None
    if mode is not RequestDataMode.PRODUCTION_LIVE:
        return DecisionPacket("NVDA", day, "WAIT", ("PRODUCTION_LIVE_MODE_REQUIRED",),
                              profile.to_dict(), 0, 0, 0, decision_status="NOT_EVALUATED").to_dict()
    provider_mode = getattr(provider, "data_mode", RequestDataMode.PRODUCTION_LIVE)
    if str(provider_mode) != RequestDataMode.PRODUCTION_LIVE.value:
        return DecisionPacket("NVDA", day, "WAIT", ("PRODUCTION_LIVE_PROVIDER_REQUIRED",),
                              profile.to_dict(), 0, 0, 0, decision_status="NOT_EVALUATED").to_dict()
    freshness = getattr(provider, "freshness", None)
    if freshness is not None and not freshness(day):
        return DecisionPacket("NVDA", day, "WAIT", ("LIVE_DATA_STALE",), profile.to_dict(), 0, 0, 0,
                              decision_status="NOT_EVALUATED").to_dict()
    try:
        share_rows = provider.get_share_position("NVDA", day)
        option_rows = provider.get_open_option_positions("NVDA", day)
    except Exception:
        return DecisionPacket("NVDA", day, "WAIT", ("LIVE_DATA_UNAVAILABLE",),
                              resolve_covered_call_profile("NVDA").to_dict(), 0, 0, 0,
                              decision_status="NOT_EVALUATED").to_dict()
    shares = sum(int(_value(p, "shares", 0) or 0) for p in share_rows)
    open_calls = sum(int(_value(p, "contracts", 0) or 0) for p in option_rows
                     if str(_value(p, "option_type", _value(p, "asset_type", ""))).upper() in {"CALL", "OPTION"}
                     and str(_value(p, "side", "SHORT")).upper() in {"SHORT", "SELL"})
    capacity = max(0, min(shares // 100 - open_calls, int(profile.max_calls)))
    base = dict(symbol="NVDA", as_of=day, profile=profile.to_dict(), shares=shares,
                open_calls=open_calls, available_capacity=capacity)
    def packet(action, codes, selected=None, gates=None):
        return DecisionPacket(action=action, reason_codes=tuple(codes), selected_contract=selected,
                              gate_results=gates, **base).to_dict()
    if profile.status is not ProfileStatus.VALIDATED:
        return packet("WAIT", ["PROFILE_NOT_VALIDATED"])
    if capacity <= 0:
        return packet("WAIT", ["COVERED_CALL_CAPACITY_UNAVAILABLE"])
    try:
        quote = provider.get_underlying_quote("NVDA", day)
        chain = provider.get_call_chain("NVDA", (14, 35), day)
        event_risk = provider.get_event_risk("NVDA", day)
    except Exception:
        return packet("WAIT", ["LIVE_PROVIDER_UNAVAILABLE"])
    spot = float(_value(quote, "price", _value(quote, "close", 0)) or 0)
    if spot <= 0: return packet("WAIT", ["QUOTE_STALE"])
    if not chain: return packet("WAIT", ["OPTION_CHAIN_UNAVAILABLE"])
    if event_risk is None: return packet("WAIT", ["EVENT_DATA_UNAVAILABLE"])
    candidates = []
    for c in chain:
        exp = str(_value(c, "expiration", ""))[:10]
        dte = int(_value(c, "dte", 0) or 0)
        strike = float(_value(c, "strike", 0) or 0); bid = float(_value(c, "bid", 0) or 0)
        ask = float(_value(c, "ask", 0) or 0)
        if str(_value(c, "option_type", _value(c, "call_put", "CALL"))).upper() not in {"CALL", "C"}: continue
        if not (14 <= dte <= 35) or bid <= 0 or ask < bid: continue
        otm = strike / spot - 1
        if abs(otm - .125) > .0125: continue
        if _value(c, "price_basis", "MARKET_RAW") != "MARKET_RAW": continue
        candidates.append((abs(otm - .125) + abs(dte - 30) / 1000, c, otm, dte))
    if not candidates:
        return packet("WAIT", ["NO_VALID_12_5_OTM_30_DTE_CALL"])
    _, chosen, otm, dte = min(candidates, key=lambda x: x[0])
    selected = {"expiration": str(_value(chosen, "expiration")), "strike": float(_value(chosen, "strike")),
                "dte": dte, "otm": otm, "bid": float(_value(chosen, "bid")),
                "ask": float(_value(chosen, "ask")), "price_basis": "MARKET_RAW"}
    gates = {}
    for name in ("liquidity", "event", "ticker_risk", "assignment"):
        fn = getattr(provider, f"check_{name}", None)
        if fn is None: return packet("WAIT", [f"{name.upper()}_GATE_UNAVAILABLE"], selected, gates)
        gates[name] = fn("NVDA", chosen)
        if not gates[name].get("pass", False): return packet("WAIT", [f"{name.upper()}_GATE_FAILED"], selected, gates)
    return packet("SELL_CALL", ["PROFILE_FROZEN", "CAPACITY_AVAILABLE", "COMMON_LIVE_SELECTION",
                                "SHARES_PRESERVED", "ASSIGNMENT_DISALLOWED", "ALL_GATES_PASS"], selected, gates)
