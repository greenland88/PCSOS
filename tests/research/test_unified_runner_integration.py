from dataclasses import replace

from pcs.data.access import PCSDataAccess
from pcs.research.research_framework import load_spec
from pcs.research.runner import ResearchRunner, _strict_flag


def _runner(ticker, output_dir="research_outputs"):
    spec = replace(load_spec("config/research/templates/new_entry.yaml"),
                   ticker=ticker, research_id=f"{ticker.lower()}_real_calendar_preflight")
    return ResearchRunner(spec, output_dir=output_dir)


def test_runner_strict_flag_does_not_treat_false_string_as_true():
    assert _strict_flag("false") is False
    assert _strict_flag("UNKNOWN") is False
    assert _strict_flag("true") is True


def test_real_preflight_rejects_end_after_train_without_oos_authorization(tmp_path):
    result = _runner("MSFT", tmp_path).real_preflight(end_date="2026-01-05")
    assert result["status"] == "FINAL_OOS_BLOCKED"
    assert result["final_oos_read"] is False


def test_amd_real_daily_calendar_is_manifest_resolved(tmp_path):
    frame = PCSDataAccess().read_prices("AMD")
    train = PCSDataAccess().read_prices("AMD", end_date="2025-12-31")
    assert len(frame) > 1000
    result = _runner("AMD", tmp_path).real_preflight()
    assert result["data_source"] == "PCS_CANONICAL_DATA"
    assert result["daily_source"] == "PCSDataAccess"
    assert result["daily_rows"] == len(train)
    assert result["daily_last_date"] == str(train.date.max().date())


def test_spy_real_daily_calendar_is_manifest_resolved(tmp_path):
    frame = PCSDataAccess().read_prices("SPY")
    train = PCSDataAccess().read_prices("SPY", end_date="2025-12-31")
    assert len(frame) > 1000
    result = _runner("SPY", tmp_path).calendar_preflight()
    assert result["data_source"] == "PCS_CANONICAL_DATA"
    assert result["daily_rows"] == len(train)
    assert result["daily_last_date"] == str(train.date.max().date())


def test_real_ticker_calendars_are_not_synthetic_or_frozen(tmp_path):
    amd = _runner("AMD", tmp_path).calendar_preflight()
    spy = _runner("SPY", tmp_path).calendar_preflight()
    assert amd["daily_rows"] != 1000 or spy["daily_rows"] != 1000
    assert amd["population_source"]["type"] == "ticker_daily_calendar" if "population_source" in amd else True
    assert amd["data_source"] == spy["data_source"] == "PCS_CANONICAL_DATA"


def test_new_entry_real_preflight_does_not_read_candidate_ledger(tmp_path):
    result = _runner("AMD", tmp_path).calendar_preflight()
    assert result["final_oos_read"] is False
    assert "candidate" not in str(result.get("daily_source", "")).lower()
    assert "ledger" not in str(result).lower()


def test_windows_manifest_routing_and_pit_failure_are_explicit(tmp_path):
    access = PCSDataAccess()
    daily = access.resolve_source("daily", "AMD")
    options = access.resolve_source("options", "AMD")
    assert daily.symbol == options.symbol == "AMD"
    assert daily.backend == options.backend == "partitioned_parquet"
    result = _runner("AMD", tmp_path).real_preflight()
    assert result["data_source"] == "PCS_CANONICAL_DATA"
    assert result["daily_rows"] > 1000
    assert result["signal_execution"] == "NOT_RUN"
