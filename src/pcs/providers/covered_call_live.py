"""Adapter from the existing broker provider to the generic CC live contract."""
from __future__ import annotations
from typing import Any
from .base import BaseBrokerProvider, CoveredCallLiveProvider


class BrokerCoveredCallLiveAdapter(CoveredCallLiveProvider):
    """Thin read-only adapter; strategy selection remains in production code."""
    def __init__(self, broker: BaseBrokerProvider):
        self.broker = broker

    def get_underlying_quote(self, symbol, as_of):
        return self.broker.get_equity_quote(symbol)

    def get_share_position(self, symbol, as_of):
        rows = self.broker.get_positions()
        return [p for p in rows if str(getattr(p, "symbol", p.get("symbol", ""))).upper() == symbol.upper()
                and str(getattr(p, "asset_type", p.get("asset_type", "EQUITY"))).upper() in {"EQUITY", "STOCK"}]

    def get_open_option_positions(self, symbol, as_of):
        rows = self.broker.get_positions()
        return [p for p in rows if str(getattr(p, "symbol", p.get("symbol", ""))).upper() == symbol.upper()
                and str(getattr(p, "asset_type", p.get("asset_type", "OPTION"))).upper() in {"OPTION", "CALL", "PUT"}]

    def get_call_chain(self, symbol, expiration_window, as_of):
        return self.broker.get_option_chain(symbol)

    def get_event_risk(self, symbol, as_of):
        getter = getattr(self.broker, "get_event_risk", None)
        if getter is None:
            return None
        try:
            return getter(symbol, as_of)
        except TypeError:
            return getter(symbol)

    def _fresh(self, as_of):
        client = getattr(self.broker, "client", None)
        checker = getattr(client, "freshness", None)
        return checker(as_of) if checker else True

    def freshness(self, as_of):
        return self._fresh(as_of)

    def _check(self, name, symbol, contract):
        fn = getattr(self.broker, f"check_{name}", None)
        return fn(symbol, contract) if fn else {"pass": False, "reason_code": f"{name.upper()}_GATE_UNAVAILABLE"}

    def check_liquidity(self, symbol, contract): return self._check("liquidity", symbol, contract)
    def check_ticker_risk(self, symbol, contract): return self._check("ticker_risk", symbol, contract)
    def check_assignment(self, symbol, contract): return self._check("assignment", symbol, contract)
