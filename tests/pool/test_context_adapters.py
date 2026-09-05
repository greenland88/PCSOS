from types import SimpleNamespace
import json
import pytest
from pcs.pool.adapters import PoolContextAdapters, load_pool_context_adapters
from pcs.engine.decision_engine import load_rules


def fixture():
    def record(data):
        return {"source_id": "test-source", "as_of": "2026-09-04", "data": data}
    payload = {"schema_version": 1, "symbols": {"NVDA": {
        "events": record([]) | {"coverage_end": "2026-10-31"},
        "portfolio": record({"planned_loss": 1000, "theoretical_max_loss": 2000,
            "bucket_risk": {"semiconductor": 1000}, "ticker_risk": {"NVDA": 500}, "account_capital": 20000})}}}
    row = SimpleNamespace(selected_contract={"entry_date": "2026-09-04", "expiration": "2026-10-09"},
        selection_result={"decision": {"action": "OPEN", "recommended_contracts": 1, "planned_loss": 100},
                          "correlation_bucket": "semiconductor"})
    return payload, row


def test_source_backed_context_and_empty_calendar():
    payload, row = fixture()
    adapter = PoolContextAdapters(payload, load_rules())
    assert adapter.event_status("NVDA", row) == "EVENT_PASS"
    assert adapter.portfolio_status("NVDA", row) == "PORTFOLIO_PASS"
    assert adapter.event_status("MSFT", row) != "EVENT_PASS"
    assert adapter.portfolio_status("MSFT", row) != "PORTFOLIO_PASS"


@pytest.mark.parametrize("mutation", ["missing", "stale", "coverage", "pit", "crossing"])
def test_event_invalid_or_crossing_never_passes(mutation):
    payload, row = fixture()
    event = payload["symbols"]["NVDA"]["events"]
    if mutation == "missing": event.pop("source_id")
    if mutation == "stale": event["as_of"] = "2026-09-03"
    if mutation == "coverage": event["coverage_end"] = "2026-09-30"
    if mutation in {"pit", "crossing"}:
        event["data"] = [{"symbol": "NVDA", "event_type": "EARNINGS", "event_date": "2026-09-20", "event_date_known_at_entry": mutation == "crossing"}]
    assert PoolContextAdapters(payload, load_rules()).event_status("NVDA", row) != "EVENT_PASS"


@pytest.mark.parametrize("mutation", ["empty", "nan", "stale", "total", "bucket", "ticker", "size"])
def test_portfolio_missing_or_incremental_risk_never_passes(mutation):
    payload, row = fixture()
    record = payload["symbols"]["NVDA"]["portfolio"]
    if mutation == "empty": record["data"] = {}
    if mutation == "nan": record["data"]["planned_loss"] = -1
    if mutation == "stale": record["as_of"] = "2026-09-03"
    if mutation == "total": record["data"]["planned_loss"] = 9950
    if mutation == "bucket": record["data"]["bucket_risk"]["semiconductor"] = 3950
    if mutation == "ticker": record["data"]["ticker_risk"]["NVDA"] = 2950
    if mutation == "size": row.selection_result = {}
    assert PoolContextAdapters(payload, load_rules()).portfolio_status("NVDA", row) != "PORTFOLIO_PASS"


def test_loader_wires_all_three_adapters(tmp_path):
    payload, _ = fixture()
    path = tmp_path / "context.json"
    path.write_text(json.dumps(payload))
    adapters = load_pool_context_adapters(path)
    assert set(adapters) == {"contract_selector", "event_status_reader", "portfolio_status_reader"}
    assert hasattr(adapters["contract_selector"], "prepare_selector")
    assert load_pool_context_adapters() == {}


def test_selector_rejects_incomplete_market_before_data_reads():
    payload, _ = fixture()
    payload["symbols"]["NVDA"]["market"] = {"source_id": "fixture", "as_of": "2026-09-04", "data": {}}
    with pytest.raises(ValueError, match="MARKET_CONTEXT_INCOMPLETE"):
        PoolContextAdapters(payload, load_rules()).prepare_selector(symbol="NVDA", day="2026-09-04",
            daily=None, handle=None, chain=None, runtime=None, access=None)


@pytest.mark.parametrize("current", [False, True])
def test_builtin_selector_calls_real_engine_and_preserves_hard_stop(monkeypatch, current):
    import pandas as pd
    import pcs.market_context as contexts
    import pcs.engine.decision_engine as engines
    from pcs.pool.options import discover_spreads, load_pool_option_rules
    from pcs.models.market import MarketState
    payload, row = fixture()
    payload["symbols"]["NVDA"]["market"] = {"source_id": "fixture", "as_of": "2026-09-04",
        "data": MarketState(vix=15).model_dump()}
    if current:
        payload.update(schema_version=2, decision_mode="CURRENT_EOD",
            decision_as_of="2026-09-05T16:00:00Z", market_data_as_of="2026-09-04")
        payload["symbols"]["NVDA"]["portfolio"]["portfolio_observed_at"]="2026-09-05T15:55:00Z"
        payload["symbols"]["NVDA"]["events"]["event_known_at"]="2026-09-05T15:50:00Z"
    # Fixture supplies already-tested upstream market/trend context. Actual
    # quote construction, hard gates, scoring and sizing execute below.
    monkeypatch.setattr(contexts, "build_market_context", lambda *a, **k: SimpleNamespace(
        underlying_price=100, atr14=2, support=95, trend_score=100, event_risk=0,
        snapshot=None, interpretation=None, score_result=None))
    monkeypatch.setattr(engines, "build_production_entry_context", lambda c: SimpleNamespace(entry_context_state="READY", reasons=()))
    chain = pd.DataFrame([dict(expiration_date=expiry, strike=strike, call_put="p",
        bid=(strike-60)/4, ask=(strike-60)/4+.05, volume=2000, open_interest=2000,
        delta=-.2, trade_date="2026-09-04")
        for expiry in ["2026-10-09", "2026-10-16", "2026-10-23", "2026-10-30", "2026-11-06"]
        for strike in range(75, 101)])
    spreads = discover_spreads("NVDA", "2026-09-04", 100, 2, chain, rules=load_pool_option_rules())
    spread = next(s for s in spreads if s.expiration == "2026-10-09" and s.short_strike == 90 and s.long_strike == 85)
    runtime = SimpleNamespace(resolve_daily_handle=lambda *a: object(), read_daily=lambda *a, **k: pd.DataFrame())
    def select():
        adapter = PoolContextAdapters(payload, load_rules())
        selector = adapter.prepare_selector(symbol="NVDA", day="2026-09-04", daily=pd.DataFrame(),
            handle=object(), chain=chain, runtime=runtime, access=None)
        return adapter, selector(spread)
    adapter, result = select()
    assert result["status"] == "PASS", result
    assert result["decision"]["recommended_contracts"] > 0
    assert result["contract"]["entry_date"] == ("2026-09-05" if current else "2026-09-04")
    if current:
        assert result["data_identity"]["market_data_as_of"] == "2026-09-04"
        assert result["data_identity"]["portfolio_observed_at"] == "2026-09-05T15:55:00Z"
    row.selected_contract, row.selection_result = result["contract"], result
    assert adapter.portfolio_status("NVDA", row) == "PORTFOLIO_PASS"
    from pcs.pool.final_gates import finalize_ticker_result
    from pcs.pool.models import TickerScanResult, EligibilityStatus, TimingStatus, OptionsStatus, FinalAction
    scanned = TickerScanResult("NVDA", "fixture", "2026-09-04", EligibilityStatus.PCS_ELIGIBLE,
        timing_status=TimingStatus.TIMING_ENTRY_READY, options_status=OptionsStatus.PASS,
        final_action=FinalAction.WAIT, selected_contract=result["contract"], selection_result=result)
    final = finalize_ticker_result(scanned, event_status=adapter.event_status("NVDA", scanned),
                                  portfolio_status=adapter.portfolio_status("NVDA", scanned))
    assert final.final_action == FinalAction.PCS_TRADE_READY
    payload["symbols"]["NVDA"]["market"]["data"]["vix"] = 35
    _, result = select()
    assert result["status"] == "REJECT"
    assert "REGIME_RED" in result["reason_codes"]


def test_read_only_worker_injects_builtin_context(tmp_path, monkeypatch):
    import pcs.pool.runner as runner
    from pcs.pool.process import ReadOnlyScanRequest, _scan_worker
    payload, _ = fixture()
    path = tmp_path / "context.json"
    path.write_text(json.dumps(payload))
    seen = {}
    def run(**kwargs):
        seen.update(kwargs)
        return "fixture-result"
    monkeypatch.setattr(runner, "run_pcs_pool", run)
    messages = []
    _scan_worker(ReadOnlyScanRequest(symbols=("NVDA",), decision_context_json=str(path)),
                 SimpleNamespace(send=messages.append, close=lambda: None))
    assert messages == [("result", "fixture-result")]
    assert isinstance(seen["contract_selector"], PoolContextAdapters)
    assert callable(seen["event_status_reader"]) and callable(seen["portfolio_status_reader"])
    assert seen["data_mode"] == "READ_ONLY" and seen["auto_prepare_data"] is False

def test_current_eod_separates_snapshot_dates_and_rejects_future():
    payload, row = fixture()
    payload.update(schema_version=2, decision_mode='CURRENT_EOD',
        decision_as_of='2026-09-05T16:00:00Z', market_data_as_of='2026-09-04')
    payload['symbols']['NVDA']['portfolio']['portfolio_observed_at']='2026-09-05T15:55:00Z'
    payload['symbols']['NVDA']['events']['event_known_at']='2026-09-05T15:50:00Z'
    adapter=PoolContextAdapters(payload,load_rules())
    adapter.bind_run('2026-09-05T16:00:00Z','EOD')
    assert adapter.event_status('NVDA',row)=='EVENT_PASS'
    assert adapter.portfolio_status('NVDA',row)=='PORTFOLIO_PASS'
    with pytest.raises(ValueError,match='IDENTITY_MISMATCH'):
        adapter.bind_run('2026-09-04T21:00:00Z','EOD')
    payload['symbols']['NVDA']['portfolio']['portfolio_observed_at']='2026-09-05T16:01:00Z'
    assert adapter.portfolio_status('NVDA',row)!='PORTFOLIO_PASS'


def test_historical_context_rejects_next_day_account():
    payload,row=fixture()
    payload['symbols']['NVDA']['portfolio']['as_of']='2026-09-05T15:55:00Z'
    assert PoolContextAdapters(payload,load_rules()).portfolio_status('NVDA',row)!='PORTFOLIO_PASS'


def test_weekend_current_eod_uses_friday():
    from pcs.pool.modes import resolve_effective_market_session
    assert str(resolve_effective_market_session('2026-09-05T16:00:00Z','EOD','XNYS').date())=='2026-09-04'
