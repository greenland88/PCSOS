from pcs.providers.codex_hood_bridge import decide_nvda_call_from_hood_trader
from datetime import datetime, timezone


class Hood:
    def get_accounts(self): return [{"id": "a"}]
    def get_positions(self): return [{"symbol": "NVDA", "shares": 100, "asset_type": "EQUITY"}]
    def get_portfolio(self): return {"value": 1000}
    def get_equity_quote(self, symbol): return {"price": 100}
    def get_option_chain(self, symbol): return [{"option_id": "c1", "option_type": "CALL", "expiration": "2026-09-27", "dte": 30, "strike": 112.5, "bid": 1.2, "ask": 1.3, "price_basis": "MARKET_RAW"}]
    def get_option_quotes(self, ids): return [{"option_id": "c1", "bid": 1.2, "ask": 1.3}]
    def get_event_risk(self, symbol): return {"status": "NO_EVENT"}


def test_codex_bridge_executes_read_only_pcs_path(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    result = decide_nvda_call_from_hood_trader(Hood(), as_of=now, output_path=tmp_path / "snapshot.json")
    assert result["action"] == "WAIT"
    assert "LIQUIDITY_GATE_UNAVAILABLE" in str(result["gate_results"])
    assert result["data_source"] == "INJECTED_LIVE_PROVIDER"
