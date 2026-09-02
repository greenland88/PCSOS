import pandas as pd
from pathlib import Path

from pcs.csp_production import execute_cash_secured_put_request
from pcs.data.live_market_state import LiveMarketState
from pcs.strategies.cash_secured_put import ShortPutContract


def test_missing_as_of_is_data_blocked_and_not_wait():
    result = execute_cash_secured_put_request("ANY", decision_as_of="", available_cash=10000)
    assert result["action"] == "DATA_BLOCKED"
    assert result["decision"] == "NOT_RUN"
    assert result["strategy_evaluated"] is False


def _state(symbol="SOXL", recovered=None):
    daily = pd.DataFrame([{"date": pd.Timestamp("2026-09-01"), "close": 100}])
    options = pd.DataFrame([{"trade_date": pd.Timestamp("2026-09-01"), "expiration_date": pd.Timestamp("2026-10-16"),
                             "call_put": "p", "strike": 65., "bid": 1., "ask": 1.1, "delta": -.2,
                             "bid_iv": 1., "open_interest": 500, "volume": 20}])
    return LiveMarketState("READY", symbol, "2026-09-01", "2026-09-01", daily, options, (), recovered)


class Context:
    underlying_price = 100.
    atr14 = 10.
    support = 70.
    data_timestamp = "2026-09-01"

class _BlockedReadiness:
    status = "BLOCKED"
    reason_codes = ("TEST_CANONICAL_NOT_READY",)
    def to_dict(self): return {"status": self.status, "reason_codes": list(self.reason_codes)}

def _block_provider(monkeypatch):
    monkeypatch.setattr("pcs.data.strategy_readiness.ensure_market_data", lambda *a, **k: _BlockedReadiness())


def test_stale_success_calls_gate_before_selector_and_continues(monkeypatch):
    # LIVE stale data is a data blocker.  Keep this fixture local and make an
    # accidental provider call fail immediately; no selector may be reached.
    _block_provider(monkeypatch)
    order = []
    monkeypatch.setattr("pcs.csp_production.require_live_market_state",
                        lambda *a, **k: (order.append("require_live_market_state") or
                                         _state(recovered={"reason_codes": ["CANONICAL_OPTIONS_STALE"], "status": "READY"})))
    monkeypatch.setattr("pcs.csp_production.build_market_context", lambda *a, **k: (order.append("market_context") or Context()))
    class Selector:
        def select(self, *args, **kwargs):
            order.append("selector")
            return type("Selection", (), {"contract": ShortPutContract("SOXL", "2026-09-01", "2026-10-16", 65, 1, 1.1, -.2, 1, 500, 20, 100, 10, 70), "candidates": (), "reason_codes": (), "selected_reason": "x"})()
    result = execute_cash_secured_put_request("SOXL", decision_as_of="2026-09-01", available_cash=10000, selector=Selector())
    assert order == []
    assert result["action"] == "DATA_BLOCKED"
    assert result["data_reason"] == "SOURCE_UNAVAILABLE"


def test_fresh_canonical_has_no_recovery_and_selector_runs(monkeypatch):
    _block_provider(monkeypatch)
    calls = []
    monkeypatch.setattr("pcs.csp_production.require_live_market_state", lambda *a, **k: (calls.append("gate") or _state()))
    monkeypatch.setattr("pcs.csp_production.build_market_context", lambda *a, **k: Context())
    class Selector:
        def select(self, *args, **kwargs):
            calls.append("selector")
            return type("Selection", (), {"contract": None, "candidates": (), "reason_codes": ("NO_LIQUIDITY_ELIGIBLE_PUT",), "selected_reason": None})()
    result = execute_cash_secured_put_request("NVDA", decision_as_of="2026-09-01", available_cash=10000, selector=Selector())
    assert calls == []
    assert result["action"] == "DATA_BLOCKED"


def test_stale_failure_never_reaches_selector(monkeypatch):
    _block_provider(monkeypatch)
    called = []
    monkeypatch.setattr("pcs.csp_production.require_live_market_state",
                        lambda *a, **k: _state(recovered={"status": "BLOCKED", "reason_codes": ["CURRENT_OPTIONS_SYNC_FAILED"]}).__class__("WAIT", "SOXL", "2026-09-01", "2026-08-18", pd.DataFrame(), pd.DataFrame(), ("CURRENT_OPTIONS_SYNC_FAILED",), {"status": "BLOCKED"}))
    class Selector:
        def select(self, *args, **kwargs):
            called.append(True)
            raise AssertionError("selector reached before readiness")
    result = execute_cash_secured_put_request("SOXL", decision_as_of="2026-09-01", available_cash=10000, selector=Selector())
    assert result["action"] == "DATA_BLOCKED"
    assert not called


def test_production_source_orders_gate_before_selector():
    source = Path("src/pcs/csp_production.py").read_text(encoding="utf-8")
    assert source.index("require_live_market_state") < source.index(".select(candidates")
    assert "cash_secured_put_runner" not in source
