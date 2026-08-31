import json
from pcs.research.cash_secured_put_runner import CashSecuredPutLifecycleRunner, REQUIRED_ARTIFACTS
from pcs.research.research_framework import load_spec
from pcs.strategies.cash_secured_put import ShortPutContract


def c(**kw):
    data = dict(symbol="SOXL", quote_date="2025-01-02", expiration="2025-01-23",
                strike=20, bid=1.0, ask=1.1, delta=-.2, iv=.8, open_interest=500,
                volume=20, underlying_price=25, atr=2, support=21)
    data.update(kw)
    return ShortPutContract(**data)


def runner():
    return CashSecuredPutLifecycleRunner(load_spec("config/research/soxl_csp_v1.yaml"))


def test_no_signal_stops_without_fabricating_dates():
    result = runner().run()
    assert result.status == "NO_SIGNAL_DATES"
    assert result.reason_codes == ("CSP_SIGNAL_ENGINE_NOT_CONFIGURED",)


def test_open_hold_profit_close_and_artifacts_are_atomic(tmp_path):
    result = runner().run(
        entries=[{"episode_id": "e1", "contract": c(), "entry_credit": 1.0}],
        daily_observations={"e1": [{"date": "2025-01-03"}, {"date": "2025-01-04", "buyback_ask": .4}]},
        output_dir=tmp_path,
    )
    assert result.lifecycle_results[0]["state"] == "PROFIT_CLOSE"
    artifact_dir = tmp_path / "SOXL_CSP_V1"
    assert {p.name for p in artifact_dir.iterdir()} == set(REQUIRED_ARTIFACTS)
    manifest = json.loads((artifact_dir / "artifact_manifest.json").read_text())
    assert manifest["current"] is True
    assert manifest["strategy_type"] == "CASH_SECURED_PUT"
    assert manifest["final_oos_read"] is False


def test_roll_then_hold_second_roll_then_close():
    first = c()
    second = c(expiration="2025-02-06", strike=19, bid=1.2, ask=1.3)
    third = c(expiration="2025-02-20", strike=18, bid=1.2, ask=1.3)
    result = runner().run(
        entries=[{"episode_id": "e1", "contract": first, "entry_credit": 1.0}],
        daily_observations={"e1": [
            {"date": "2025-01-04", "roll": "second", "old_buyback_ask": 1.0},
            {"date": "2025-01-05"},
            {"date": "2025-01-10", "roll": "third", "old_buyback_ask": 1.0},
            {"date": "2025-01-11", "buyback_ask": .5},
        ]},
        exact_quotes={"e1": {"second": second.__dict__, "third": third.__dict__}},
    )
    row = result.lifecycle_results[0]
    assert row["state"] == "PROFIT_CLOSE"
    assert row["roll_count"] == 2
    assert [x["action"] for x in row["actions"]] == ["OPEN", "ROLL", "HOLD", "ROLL", "PROFIT_CLOSE"]


def test_assignment_uses_stock_mtm_without_double_counting():
    result = runner().run(
        entries=[{"episode_id": "e1", "contract": c(), "entry_credit": 1.0}],
        daily_observations={"e1": [{"date": "2025-01-23", "expire": True, "underlying_mark": 18, "holding_days": 10}]},
    )
    assert result.lifecycle_results[0]["state"] == "ASSIGNMENT"
    assert result.assignment_ledger[0]["stock_mtm"] == -100
    assert result.assignment_ledger[0]["total_economic_pnl"] == -100


def test_missing_roll_quote_fails_closed():
    result = runner().run(
        entries=[{"episode_id": "e1", "contract": c(), "entry_credit": 1.0}],
        daily_observations={"e1": [{"date": "2025-01-04", "roll": "missing", "old_buyback_ask": 1.0}]},
    )
    assert result.lifecycle_results[0]["state"] == "HOLD"
    assert result.lifecycle_results[0]["actions"][1]["reason_code"] == "MISSING_EXACT_ROLL_QUOTE"
