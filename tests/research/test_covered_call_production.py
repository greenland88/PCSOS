from pcs.covered_call_production import decide_nvda_call_today, RequestDataMode


class Provider:
    def get_share_position(self, symbol, as_of):
        return [{"symbol": "NVDA", "shares": 250}]
    def get_open_option_positions(self, symbol, as_of): return []
    def get_underlying_quote(self, symbol, as_of): return {"price": 100.0}
    def get_call_chain(self, symbol, expiration_window, as_of):
        return [{"option_type": "CALL", "expiration": "2026-08-28", "dte": 30,
                 "strike": 112.5, "bid": 1.2, "ask": 1.3, "price_basis": "MARKET_RAW"}]
    def get_event_risk(self, symbol, as_of): return {"status": "NO_EVENT"}
    def check_liquidity(self, symbol, contract): return {"pass": True}
    def check_event(self, symbol, contract): return {"pass": True}
    def check_ticker_risk(self, symbol, contract): return {"pass": True}
    def check_assignment(self, symbol, contract): return {"pass": True}


def test_nvda_production_decision_preserves_shares_and_selects_common_basis():
    result = decide_nvda_call_today(Provider(), as_of="2026-07-01")
    assert result["action"] == "SELL_CALL"
    assert result["available_capacity"] == 1
    assert result["selected_contract"]["price_basis"] == "MARKET_RAW"


def test_nvda_production_decision_fails_closed_without_gate():
    class NoEvent(Provider):
        get_event_risk = lambda self, symbol, as_of: None
    result = decide_nvda_call_today(NoEvent(), as_of="2026-07-01")
    assert result["action"] == "WAIT"
    assert "EVENT_DATA_UNAVAILABLE" in result["reason_codes"]


def test_today_requires_production_live_mode():
    result = decide_nvda_call_today(Provider(), as_of="2026-07-01", mode=RequestDataMode.RESEARCH_PIT)
    assert result["action"] == "WAIT"
    assert result["decision_status"] == "NOT_EVALUATED"
    assert "PRODUCTION_LIVE_MODE_REQUIRED" in result["reason_codes"]


def test_today_without_live_snapshot_is_not_evaluated():
    class NoSnapshot:
        def get_share_position(self, symbol, as_of): raise RuntimeError("missing")
        def get_open_option_positions(self, symbol, as_of): raise RuntimeError("missing")
    result = decide_nvda_call_today(NoSnapshot(), as_of="2026-07-01")
    assert result["action"] == "WAIT"
    assert result["decision_status"] == "NOT_EVALUATED"
    assert result["reason_codes"] == ["LIVE_DATA_UNAVAILABLE"]


def test_stale_live_snapshot_is_not_evaluated():
    class Stale(Provider):
        freshness = lambda self, day: False
    result = decide_nvda_call_today(Stale(), as_of="2026-07-01")
    assert result["action"] == "WAIT"
    assert result["decision_status"] == "NOT_EVALUATED"
    assert result["reason_codes"] == ["LIVE_DATA_STALE"]


def test_historical_provider_cannot_be_used_for_today():
    class HistoricalOnly(Provider):
        data_mode = RequestDataMode.RESEARCH_PIT
    result = decide_nvda_call_today(HistoricalOnly(), as_of="2026-07-01")
    assert result["action"] == "WAIT"
    assert result["decision_status"] == "NOT_EVALUATED"
    assert "PRODUCTION_LIVE_PROVIDER_REQUIRED" in result["reason_codes"]


def test_live_selection_is_otm_and_dte_driven_not_delta_driven():
    class Live(Provider):
        def get_call_chain(self, symbol, expiration_window, as_of):
            return [
                {"option_type": "CALL", "expiration": "2026-07-31", "dte": 30,
                 "strike": 112.5, "bid": 1.2, "ask": 1.3, "delta": .20, "price_basis": "MARKET_RAW"},
                {"option_type": "CALL", "expiration": "2026-07-31", "dte": 30,
                 "strike": 112.0, "bid": 3.0, "ask": 3.1, "delta": .50, "price_basis": "MARKET_RAW"},
            ]
    result = decide_nvda_call_today(Live(), as_of="2026-07-01")
    assert result["action"] == "SELL_CALL"
    assert result["selected_contract"]["strike"] == 112.5
