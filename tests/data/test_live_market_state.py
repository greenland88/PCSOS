import pandas as pd

from pcs.data.live_market_state import require_live_market_state


class Access:
    def read_prices(self, symbol, start_date=None, end_date=None):
        return pd.DataFrame({"date": ["2026-08-15"], "close": [100.0]})

    def read_quotes(self, symbol, start_date, end_date, **kwargs):
        return pd.DataFrame()


def test_live_gate_failure_is_blocked_not_wait(monkeypatch):
    from pcs.data import live_market_state
    monkeypatch.setattr(live_market_state.MarketDataControlPlane,
                        "ensure_market_data", lambda self, req: {"status": "BLOCKED"})
    state = require_live_market_state("AAA", "2026-08-18", data_access=Access())
    assert state.status == "BLOCKED"
    assert "OPTIONS_FRESHNESS_GATE_FAILED" in state.reason_codes
