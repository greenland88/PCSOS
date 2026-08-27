import pandas as pd

import pcs.research.stage4a_context as context_module
from pcs.research.stage4a_context import HistoricalTrendContextProvider


def test_historical_context_provider_is_pit_and_cached(monkeypatch, tmp_path):
    root = tmp_path / "daily"
    root.mkdir()
    for name in ("NVDA", "QQQ"):
        pd.DataFrame({"date": ["2025-01-01"], "open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [10]}).to_csv(root / f"{name}_daily_qfq.csv", index=False)
    calls = []

    def producer(stock, bench, day, ticker, benchmark):
        calls.append(day)
        return {"available": True, "snapshot": {"as_of": day}, "interpretation": {"state": "SETUP_PASS"}, "trend_score": {"score": 80}, "reason_codes": []}

    monkeypatch.setattr(context_module, "build_historical_setup_context", producer)
    provider = HistoricalTrendContextProvider("NVDA", root)
    row = {"candidate_id": "c1", "date": "2025-01-01"}
    first = provider(row)
    second = provider(row)
    assert first is second
    assert len(calls) == 1
    persisted = provider.serialized(row)
    assert persisted["pit"] is True
    assert pd.Timestamp(persisted["pit_asof"]) <= pd.Timestamp(persisted["decision_date"])
    assert persisted["context_available"] is True
