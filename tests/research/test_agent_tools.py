import json
from pcs.agent.models import response
from pcs.agent import tools
from pcs.data.access import DataAccessError

def test_agent_response_is_structured_and_json_serializable():
    r=response("UNAVAILABLE","DATA_NOT_FOUND")
    assert r.module=="pcs.agent" and r.request_id and json.loads(r.to_json())["reason_code"]=="DATA_NOT_FOUND"
    payload = json.loads(r.to_json())
    assert "as_of" in payload and "run_id" in payload


def test_agent_market_data_fail_closed_on_canonical_route_error(monkeypatch):
    class BrokenAccess:
        def read_prices(self, *args, **kwargs):
            raise DataAccessError("route missing")

        def read_option_chain(self, *args, **kwargs):
            raise DataAccessError("route missing")

    monkeypatch.setattr(tools, "PCSDataAccess", BrokenAccess)
    daily = tools.get_daily_history("TEST", "2025-01-01", "2025-01-02")
    chain = tools.get_option_chain("TEST", "2025-01-02")
    assert daily.status == chain.status == "UNAVAILABLE"
    assert daily.reason_code == chain.reason_code == "CANONICAL_ROUTE_UNAVAILABLE"
    assert "DataAccessError" in daily.reason_codes
