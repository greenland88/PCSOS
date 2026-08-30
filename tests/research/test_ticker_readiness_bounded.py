from pcs.research import ticker_readiness


def test_assert_research_ready_passes_declared_bounds(monkeypatch):
    seen = {}
    class Ready:
        PCS_RESEARCH_READY = "YES"
        blockers = []
    def fake(symbol, **kwargs):
        seen.update(kwargs)
        return Ready()
    monkeypatch.setattr(ticker_readiness, "preflight_ticker", fake)
    ticker_readiness.assert_research_ready("PLTR", start_date="2020-10-20", end_date="2023-12-31")
    assert seen["start_date"] == "2020-10-20"
    assert seen["end_date"] == "2023-12-31"
