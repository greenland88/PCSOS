import pandas as pd

from pcs.research.strategy_transfer_runner import _validate_transfer_daily
from pcs.strategies.research_templates.catalog import STRATEGIES


def test_transfer_warmup_rows_are_not_treated_as_requested_window_overflow():
    frame = pd.DataFrame({
        "symbol": ["AMD"] * 3,
        "date": pd.to_datetime(["2024-12-31", "2025-01-02", "2025-01-03"]),
        "open": [1, 1, 1], "high": [2, 2, 2], "low": [0, 0, 0],
        "close": [1, 1, 1], "volume": [10, 10, 10],
    })
    _validate_transfer_daily(__import__("pcs.data.access", fromlist=["PCSDataAccess"]).PCSDataAccess(),
                             frame, "AMD", "2025-01-01", "2025-01-03")


def test_research_templates_declare_daily_only_dependencies_explicitly():
    assert STRATEGIES
    assert all(spec.data_dependencies == ("daily",) for spec in STRATEGIES.values())
