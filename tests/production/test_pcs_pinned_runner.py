import pandas as pd
import pytest

from pcs.data.access import PCSDataAccess
from pcs.pcs_status import evaluate_pcs_status


def _daily(symbol="ZZZ"):
    return pd.DataFrame({"symbol": [symbol], "date": pd.to_datetime(["2026-09-01"]),
                         "open": [100.], "high": [101.], "low": [99.],
                         "close": [100.], "volume": [1000]})


def _options(symbol="ZZZ"):
    return pd.DataFrame({"symbol": [symbol, symbol], "trade_date": pd.to_datetime(["2026-09-01"] * 2),
        "expiration_date": pd.to_datetime(["2026-10-16"] * 2), "call_put": ["p", "p"],
        "strike": [80., 75.], "bid": [1., .5], "ask": [1.1, .6],
        "open_interest": [1000, 1000], "volume": [100, 100], "delta": [-.2, -.1],
        "bid_iv": [.3, .3], "ask_iv": [.31, .31], "last": [1., .5],
        "gamma": [0., 0.], "vega": [0., 0.], "theta": [0., 0.], "rho": [0., 0.]})


class _Result:
    status = "READY"
    reason_codes = ()
    def __init__(self, receipts): self.receipts = receipts
    def to_dict(self): return {"promotion_receipt": self.receipts[0], "receipts": self.receipts}


@pytest.fixture
def ready_pcs_data_bundle(tmp_path, monkeypatch):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    daily = access.promote_generation(_daily(), "daily", "ZZZ", "year=2026", source_version="fixture-daily")
    options = access.promote_generation(_options(), "options", "ZZZ", "year=2026/quarter=3", source_version="fixture-options")
    receipts = [daily.to_dict(), options.to_dict()]
    calls = {"provider": 0, "pinned": [], "legacy": 0, "selector": 0}

    def provider_forbidden(*args, **kwargs):
        calls["provider"] += 1
        raise AssertionError("provider called during PCS execution")
    monkeypatch.setattr("pcs.data.strategy_readiness.ensure_market_data", provider_forbidden)
    original = access.read_pinned_generation
    def pinned(*args, **kwargs):
        calls["pinned"].append((args[0], args[3]))
        try:
            return original(*args, **kwargs)
        except Exception as exc:
            raise
    monkeypatch.setattr(access, "read_pinned_generation", pinned)
    return access, receipts, calls, daily.generation_id, options.generation_id


def _run_ready(monkeypatch, access, calls, selector=None):
    monkeypatch.setattr("pcs.pcs_status.require_live_market_state", lambda *a, **k: type("L", (), {"status": "READY", "reason_codes": (), "required_session": "2026-09-01", "recovery": {}, "options": _options(), "daily": _daily()})())
    monkeypatch.setattr("pcs.pcs_status.build_market_context", lambda *a, **k: type("C", (), {"underlying_price": 100., "atr14": 10., "support": 70., "event_risk": "LOW", "trend_score": 0., "data_timestamp": "2026-09-01", "snapshot": {}, "interpretation": "", "score_result": None, "market_state": None})())
    monkeypatch.setattr("pcs.pcs_status.generate_structural_put_opportunities", lambda chain, *a: (calls.__setitem__("selector", calls["selector"] + 1) or []))
    return evaluate_pcs_status("ZZZ", "2026-09-01", data_access=access, event_calendar=pd.DataFrame([{"symbol":"ZZZ", "event_date":"2026-12-01", "event_type":"EARNINGS", "event_date_known_at_entry":"YES"}]))


def test_pcs_non_ready_never_calls_selector(monkeypatch, ready_pcs_data_bundle):
    access, _, calls, *_ = ready_pcs_data_bundle
    monkeypatch.setattr("pcs.data.strategy_readiness.ensure_market_data", lambda *a, **k: (_ for _ in ()).throw(AssertionError("provider must not run")))
    result = evaluate_pcs_status("ZZZ", "2026-09-01", data_access=access)
    assert result.action is None and result.reason_codes == ["DATA_BLOCKED"]
    assert calls["selector"] == 0


def test_pcs_reads_both_pinned_generations(monkeypatch, ready_pcs_data_bundle):
    access, receipts, calls, daily_gen, options_gen = ready_pcs_data_bundle
    monkeypatch.setattr("pcs.data.strategy_readiness.ensure_market_data", lambda *a, **k: _Result(receipts))
    result = _run_ready(monkeypatch, access, calls)
    assert {x[0] for x in calls["pinned"]} == {"daily", "options"}
    assert result.readiness_underlying_generation_id == daily_gen
    assert result.readiness_options_generation_id == options_gen


def test_pcs_result_records_both_runner_generations(monkeypatch, ready_pcs_data_bundle):
    access, receipts, calls, daily_gen, options_gen = ready_pcs_data_bundle
    monkeypatch.setattr("pcs.data.strategy_readiness.ensure_market_data", lambda *a, **k: _Result(receipts))
    result = _run_ready(monkeypatch, access, calls)
    assert result.runner_underlying_generation_id == result.readiness_underlying_generation_id == daily_gen
    assert result.runner_options_generation_id == result.readiness_options_generation_id == options_gen
    assert calls["provider"] == 0
