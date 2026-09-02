import pandas as pd

from pcs.covered_call_executor import execute_covered_call_request
from pcs.research.covered_call import CoveredCallContract


class Access:
    def read_prices(self, symbol, end_date=None):
        dates = pd.date_range("2026-01-01", periods=40, freq="D")
        close = [100 + i * .5 for i in range(40)]
        return pd.DataFrame({"symbol": symbol, "date": dates, "open": close,
                             "high": [x + 1 for x in close], "low": [x - 1 for x in close],
                             "close": close, "volume": [100000] * 40})


def test_executor_prepares_all_dependencies_and_reaches_strategy():
    quote = CoveredCallContract("HOOD", "2026-02-09", "2026-03-16", 130, 2, 2.2,
                                delta=.20, open_interest=500, volume=20)
    trace = []
    result = execute_covered_call_request(
        "HOOD", "eod", as_of="2026-02-09", research_only=True,
        adapters={
            "data_access": Access(),
            "ensure_data": lambda s, d, t: t.add("CANONICAL_DATA", "REFRESHED"),
            "position_loader": lambda s, d: {"shares_owned": 100, "active_calls": 0, "source": "fake"},
            "market_builder": lambda s, d: {"market_state": "NORMAL"},
            "event_loader": lambda s, d: {"earnings_status": "NO_EVENT"},
            "chain_loader": lambda s, d: [quote],
        })
    # A hand-written Access without canonical readiness cannot enter the
    # strategy.  The public executor must fail closed rather than treating
    # adapter-provided rows as a substitute for readiness.
    assert result["system_status"] == "BLOCKED"
    assert result["strategy_evaluated"] is False
    assert result["strategy_status"] == "NOT_RUN"
