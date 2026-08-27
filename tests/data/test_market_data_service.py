from pcs.data.market_data_service import MarketDataReason, MarketDataService, MarketDataStatus


class Client:
    def stock_snapshot(self, symbol):
        return {"ticker": symbol}

    def stock_last_quote(self, symbol):
        return {"P": 101, "p": 100}

    def stock_last_trade(self, symbol):
        return {"p": 100.5}

    def option_chain_snapshot(self, symbol, limit, max_pages):
        return [{"details": {"ticker": f"O:{symbol}"}}]


def test_agent_ready_stock_envelope():
    result = MarketDataService(Client()).get_stock_realtime("aapl", run_id="run_1", request_id="req_1")
    assert result.status == MarketDataStatus.READY
    assert result.symbol == "AAPL"
    assert result.run_id == "run_1"
    assert result.request_id == "req_1"
    assert result.reason_codes == [MarketDataReason.DATA_AVAILABLE]
    assert result.model_dump(mode="json")["module"] == "pcs.market_data_gateway"


def test_option_chain_is_typed_and_bounded_by_service_call():
    result = MarketDataService(Client()).get_option_chain_realtime("nvda", limit=10, max_pages=2)
    assert result.status == MarketDataStatus.READY
    assert result.data[0]["details"]["ticker"] == "O:NVDA"


def test_stock_realtime_returns_partial_data_when_one_component_fails():
    from pcs.data.massive_client import MarketGatewayError

    class PartialClient(Client):
        def stock_snapshot(self, symbol):
            raise MarketGatewayError("timeout")

    result = MarketDataService(PartialClient()).get_stock_realtime("AAPL")
    assert result.status == MarketDataStatus.READY
    assert result.data["unavailable_components"] == ["snapshot"]
    assert MarketDataReason.PARTIAL_DATA in result.reason_codes
