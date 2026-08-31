import json
import pytest
from pcs.research.cash_secured_put_runner import CashSecuredPutLifecycleRunner, REQUIRED_ARTIFACTS, read_csp_artifacts
from pcs.research.research_framework import load_spec
from pcs.strategies.cash_secured_put import ShortPutContract


def c(**kw):
    data = dict(symbol="SOXL", quote_date="2025-01-02", expiration="2025-01-23",
                strike=20, bid=1.0, ask=1.1, delta=-.2, iv=.8, open_interest=500,
                volume=20, underlying_price=25, atr=2, support=21)
    data.update(kw)
    return ShortPutContract(**data)


def runner(data_access=None):
    return CashSecuredPutLifecycleRunner(load_spec("config/research/soxl_csp_v1.yaml"), data_access=data_access)


def test_no_signal_stops_without_fabricating_dates():
    result = runner().run()
    assert result.status == "NO_SIGNAL_DATES"
    assert result.reason_codes == ("CSP_SIGNAL_ENGINE_NOT_CONFIGURED",)


def test_open_hold_profit_close_and_artifacts_are_atomic(tmp_path):
    result = runner(data_access=object()).run(
        entries=[{"episode_id": "e1", "contract": c(), "entry_credit": 1.0}],
        daily_observations={"e1": [{"date": "2025-01-03"}, {"date": "2025-01-04", "buyback_ask": .4}]},
        trading_sessions_by_episode={"e1": ["2025-01-03", "2025-01-04"]},
        output_dir=tmp_path,
    )
    assert result.lifecycle_results[0]["state"] == "PROFIT_CLOSE"
    artifact_dir = tmp_path / "SOXL_CSP_V1"
    assert {p.name for p in artifact_dir.iterdir()} == set(REQUIRED_ARTIFACTS)
    manifest = json.loads((artifact_dir / "artifact_manifest.json").read_text())
    assert manifest["current"] is True
    assert manifest["strategy_type"] == "CASH_SECURED_PUT"
    assert manifest["final_oos_read"] is False
    with pytest.raises(RuntimeError, match="STALE_ARTIFACT"):
        read_csp_artifacts(artifact_dir, runner(data_access=object()).spec)
    (artifact_dir / "yearly_metrics.json").write_text("{}")
    with pytest.raises(RuntimeError, match="STALE_ARTIFACT"):
        read_csp_artifacts(artifact_dir, runner(data_access=object()).spec)


def test_roll_then_hold_second_roll_then_close():
    first = c()
    second = c(quote_date="2025-01-04", expiration="2025-02-06", strike=19, bid=1.2, ask=1.3)
    third = c(quote_date="2025-01-10", expiration="2025-02-20", strike=18, bid=1.2, ask=1.3)
    result = runner().run(
        entries=[{"episode_id": "e1", "contract": first, "entry_credit": 1.0}],
        daily_observations={"e1": [
            {"date": "2025-01-04", "roll": "second", "old_buyback_ask": 1.0},
            {"date": "2025-01-05"},
            {"date": "2025-01-10", "roll": "third", "old_buyback_ask": 1.0},
            {"date": "2025-01-11", "buyback_ask": .5},
        ]},
        exact_quotes={"e1": {"second": second.__dict__, "third": third.__dict__}},
        trading_sessions_by_episode={"e1": ["2025-01-04", "2025-01-05", "2025-01-10", "2025-01-11"]},
    )
    row = result.lifecycle_results[0]
    assert row["state"] == "PROFIT_CLOSE"
    assert row["roll_count"] == 2
    assert [x["action"] for x in row["actions"]] == ["OPEN", "ROLL", "HOLD", "ROLL", "PROFIT_CLOSE"]
    assert row["original_entry_date"] == "2025-01-02"
    assert row["original_strike"] == 20
    assert row["actions"][1]["old_strike"] == 20
    assert row["actions"][1]["new_strike"] == 19
    assert row["actions"][3]["old_strike"] == 19
    assert row["actions"][3]["new_strike"] == 18


def test_assignment_uses_stock_mtm_without_double_counting():
    result = runner().run(
        entries=[{"episode_id": "e1", "contract": c(), "entry_credit": 1.0}],
        daily_observations={"e1": [{"date": "2025-01-23", "expire": True, "underlying_mark": 18, "holding_days": 10}]},
        trading_sessions_by_episode={"e1": ["2025-01-23"]},
    )
    assert result.lifecycle_results[0]["state"] == "ASSIGNMENT"
    assert result.assignment_ledger[0]["stock_mtm"] == -100
    assert result.assignment_ledger[0]["total_economic_pnl"] == -100
    row = result.lifecycle_results[0]
    assert row["gross_premium_received"] == 100
    assert row["net_option_pnl"] == 100
    assert row["assignment_stock_component"] == -200
    assert row["total_economic_pnl"] == -100


def test_time_metrics_use_dates_and_explicit_pit_sessions():
    result = runner().run(
        entries=[{"episode_id": "e1", "contract": c(), "entry_credit": 1.0}],
        daily_observations={"e1": [
            {"date": "2025-01-03", "is_trading_session": True},
            {"date": "2025-01-06", "is_trading_session": True},
        ]},
        trading_sessions_by_episode={"e1": ["2025-01-03", "2025-01-06"]},
    )
    row = result.lifecycle_results[0]
    assert row["holding_calendar_days"] == 4
    assert row["holding_trading_days"] == 2
    assert row["collateral_calendar_days"] == row["collateral_required"] * 4
    assert row["collateral_trading_days"] == row["collateral_required"] * 2


def test_missing_roll_quote_fails_closed():
    result = runner().run(
        entries=[{"episode_id": "e1", "contract": c(), "entry_credit": 1.0}],
        daily_observations={"e1": [{"date": "2025-01-04", "roll": "missing", "old_buyback_ask": 1.0}]},
        trading_sessions_by_episode={"e1": ["2025-01-04"]},
    )
    assert result.lifecycle_results[0]["state"] == "HOLD"
    assert result.lifecycle_results[0]["actions"][1]["reason_codes"] == ["MISSING_EXACT_ROLL_QUOTE"]
