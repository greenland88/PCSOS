from pcs.research.cash_secured_put_runner import CashSecuredPutLifecycleRunner
from pcs.research.research_framework import load_spec
from pcs.strategies.cash_secured_put import ShortPutContract


def contract(**overrides):
    data = dict(symbol="SOXL", quote_date="2025-01-02", expiration="2025-01-23",
                strike=20, bid=1.0, ask=1.1, delta=-.2, iv=.8, open_interest=500,
                volume=20, underlying_price=25, atr=2, support=21, pit_status="PIT_SAFE")
    data.update(overrides)
    return ShortPutContract(**data)


def test_explicit_sessions_and_two_roll_segments_preserve_entry_identity():
    second = contract(quote_date="2025-01-06", expiration="2025-02-06", strike=19, bid=1.2, ask=1.3)
    third = contract(quote_date="2025-01-10", expiration="2025-02-20", strike=18, bid=1.2, ask=1.3)
    result = CashSecuredPutLifecycleRunner(load_spec("config/research/soxl_csp_v1.yaml")).run(
        entries=[{"episode_id": "e", "contract": contract(), "entry_credit": 1.0}],
        daily_observations={"e": [{"date": "2025-01-06", "roll": "b", "old_buyback_ask": 1.0},
                                  {"date": "2025-01-10", "roll": "c", "old_buyback_ask": 1.0},
                                  {"date": "2025-01-13", "buyback_ask": .5}]},
        exact_quotes={"e": {"b": second.__dict__, "c": third.__dict__}},
        trading_sessions_by_episode={"e": ["2025-01-03", "2025-01-06", "2025-01-07", "2025-01-10", "2025-01-13"]},
    )
    row = result.lifecycle_results[0]
    assert row["original_entry_date"] == "2025-01-02"
    assert [s["strike"] for s in result.collateral_segments] == [20, 19, 18]
    assert [s["start_date"] for s in result.collateral_segments] == ["2025-01-02", "2025-01-06", "2025-01-10"]
    assert [s["trading_days"] for s in result.collateral_segments] == [1, 2, 2]


def test_missing_session_calendar_fails_closed():
    runner = CashSecuredPutLifecycleRunner(load_spec("config/research/soxl_csp_v1.yaml"))
    try:
        runner.run(entries=[{"contract": contract(), "entry_credit": 1.0}],
                   daily_observations={"SOXL:0": [{"date": "2025-01-03"}]})
    except ValueError as exc:
        assert str(exc) == "SESSION_CALENDAR_UNAVAILABLE"
    else:
        raise AssertionError("missing calendar must fail closed")
