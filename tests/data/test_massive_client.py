import pandas as pd

from pcs.data.massive_client import GatewayConfig, MassiveCompatibleClient


class Response:
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
