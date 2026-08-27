import pytest

from pcs.covered_call_research.runner import CallQuote, CoveredCallRunner, ReplayStatus


def q(day, exp, strike, bid, ask):
    return CallQuote("PLTR", day, exp, strike, bid, ask)


def test_runner_uses_bid_ask_and_models_roll_as_two_legs():
    r = CoveredCallRunner("PLTR", max_short_calls=3)
    lot = r.open_call(q("2024-01-02", "2024-02-16", 20, 1.0, 1.2))
    r.roll(lot, q("2024-01-10", "2024-02-16", 20, 0.4, 0.6),
          q("2024-01-10", "2024-03-15", 21, 0.8, 1.0))
    assert lot.premium_received == pytest.approx(180.0)
    assert lot.buyback_cost == pytest.approx(60.0)
    assert lot.roll_credit == pytest.approx(20.0)
    assert r.actions[-1]["action"] == "ROLL"


def test_runner_fail_closes_itm_expiry_instead_of_selling_stock():
    r = CoveredCallRunner("PLTR")
    lot = r.open_call(q("2024-01-02", "2024-01-03", 20, 1.0, 1.2))
    r.observe("2024-01-03", 21.0, {lot.lot_id: q("2024-01-03", "2024-01-03", 20, 0.0, 1.5)})
    result = r.result()
    assert result.status == ReplayStatus.BLOCKED
    assert any("ASSIGNMENT_RISK" in x for x in result.reason_codes)
    assert not any(x.get("action") == "SELL_STOCK" for x in result.actions)
