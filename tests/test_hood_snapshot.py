from pcs.providers.hood_snapshot import generate_hoodtrader_snapshot
from pcs.providers.hood_trader_provider import HoodTraderProvider, JsonHoodClient


class Runtime:
    def get_accounts(self): return [{"id": "a"}]
    def get_positions(self): return [{"symbol": "NVDA", "shares": 100, "asset_type": "EQUITY"}]
    def get_portfolio(self): return {"value": 1000}
    def get_equity_quote(self, symbol): return {"price": 100}
    def get_option_chain(self, symbol): return [{"option_id": "c1", "strike": 112.5}]
    def get_option_quotes(self, ids): return [{"option_id": i, "bid": 1} for i in ids]
    def get_event_risk(self, symbol): return {"status": "NO_EVENT"}


def test_runtime_snapshot_round_trip_and_atomic_write(tmp_path):
    target = tmp_path / "data" / "live" / "hoodtrader_snapshot.json"
    snap = generate_hoodtrader_snapshot(Runtime(), ["NVDA"], "2026-08-28T10:00:00+00:00", output_path=target)
    assert target.exists()
    assert snap.option_quotes["NVDA"][0]["option_id"] == "c1"
    client = JsonHoodClient(snap)
    provider = HoodTraderProvider(client)
    assert provider.get_equity_quote("NVDA")["price"] == 100
    assert provider.get_option_quotes(["c1"])[0]["bid"] == 1
