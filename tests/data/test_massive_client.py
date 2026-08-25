import os

from pcs.data.massive_client import GatewayConfig, MassiveCompatibleClient


class Response:
    status_code = 200
    headers = {}

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": [{"t": 1762128000000, "o": 270, "h": 275, "l": 269, "c": 274, "v": 1000}]}


class Session:
    def __init__(self):
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        return Response()


def test_stock_daily_uses_private_gateway_and_normalizes_bars():
    session = Session()
    client = MassiveCompatibleClient(GatewayConfig("secret"), session)
    frame = client.stock_daily("aapl", "2025-11-03", "2025-11-28")
    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert frame.iloc[0].close == 274
    assert session.calls[0][0] == "http://38.76.185.106:3000/v2/aggs/ticker/AAPL/range/1/day/2025-11-03/2025-11-28"
    assert session.calls[0][1]["apiKey"] == "secret"


def test_pagination_rejects_public_or_foreign_next_url():
    class PagingResponse(Response):
        def json(self):
            return {"results": [{"ticker": "O:AAPL"}], "next_url": "https://api.massive.com/v3/options"}

    session = Session()
    session.get = lambda *args, **kwargs: PagingResponse()
    client = MassiveCompatibleClient(GatewayConfig("secret"), session)
    try:
        list(client.iter_results("/v3/reference/options/contracts", {}, max_pages=2))
        assert False, "foreign pagination URL must fail closed"
    except Exception as exc:
        assert "escaped the configured private gateway" in str(exc)


def test_config_reads_env_file_without_overriding_process_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("PCS_MARKET_DATA_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.delenv("PCS_MARKET_DATA_API_KEY", raising=False)
    assert GatewayConfig.from_environment(env_file).api_key == "file-key"
    monkeypatch.setenv("PCS_MARKET_DATA_API_KEY", "process-key")
    assert GatewayConfig.from_environment(env_file).api_key == "process-key"
