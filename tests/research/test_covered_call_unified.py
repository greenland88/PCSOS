import pytest
import pandas as pd

from pcs.research.covered_call import (
    CoveredCallContract, CoveredCallDailyEngine, CoveredCallEpisode,
    CoveredCallPositionBook, CoveredCallRollSelector,
)
from pcs.research.covered_call_research import ReplayQuoteProvider


def call(qdate, expiry, strike, bid, ask):
    return CoveredCallContract("NVDA", qdate, expiry, strike, bid, ask,
                               open_interest=500, volume=20)


def test_episode_roll_chain_preserves_accounting():
    e = CoveredCallEpisode("E1", "NVDA", 100, call("2024-12-20", "2025-01-10", 110, 2, 2.2))
    e.roll(call("2025-01-07", "2025-02-10", 115, 3, 3.2), old_buyback_price=1)
    assert e.episode_id == "E1"
    assert e.cumulative_premium_received == 500
    assert e.cumulative_buyback_cost == 100
    assert e.cumulative_roll_credits == 200
    assert e.close(close_date="2025-02-03", underlying_price=100, buyback_price=.5) == 350
    assert e.final_pnl == 350


def test_losing_btc_uses_exact_ask_and_realizes_negative_option_pnl():
    e = CoveredCallEpisode("E1", "NVDA", 100, call("2024-12-20", "2025-01-10", 110, 2, 2.2))
    e.close(close_date="2024-12-23", underlying_price=100, buyback_price=3)
    assert e.realized_cashflow - (100 - 100) * 100 == pytest.approx(-100.0)


def test_permanent_shares_forces_itm_expiration_btc_instead_of_assignment():
    c = call("2025-01-02", "2025-01-10", 100, 2, 2.2)
    result = CoveredCallDailyEngine("NVDA").run([
        {"date": "2025-01-02", "close": 98, "new_entry": True, "entry_contract": c},
        {"date": "2025-01-09", "close": 110},
        {"date": "2025-01-10", "close": 110},
    ], quotes_by_date={"2025-01-02": [c], "2025-01-09": [
        call("2025-01-09", "2025-01-10", 100, 8, 9)]})
    episode = result["episodes"][0]
    assert episode.closed and episode.forced_btc and not episode.assigned
    assert result["actions"][-1]["action"] == "FORCED_BTC_TO_PROTECT_SHARES"


def test_forced_btc_loss_is_real_option_loss_and_shares_remain_owned():
    c = call("2025-01-02", "2025-01-10", 100, 2, 2.2)
    expensive = call("2025-01-09", "2025-01-10", 100, 2.5, 3)
    result = CoveredCallDailyEngine("NVDA").run([
        {"date": "2025-01-02", "close": 98, "new_entry": True, "entry_contract": c},
        {"date": "2025-01-09", "close": 110}, {"date": "2025-01-10", "close": 110}],
        quotes_by_date={"2025-01-02": [c], "2025-01-09": [expensive]})
    episode = result["episodes"][0]
    stock_pnl = (100 - episode.stock_entry_price) * episode.shares
    assert episode.forced_btc and episode.realized_cashflow - stock_pnl == pytest.approx(-100.0)
    assert episode.assigned is False


def test_active_contract_quotes_from_cache_without_storage_read():
    provider = ReplayQuoteProvider()
    frame = pd.DataFrame({"symbol": ["NVDA"], "trade_date": ["2024-01-03"],
        "expiration_date": ["2024-02-02"], "strike": [110.], "call_put": ["c"],
        "bid": [2.], "ask": [2.2], "delta": [.2], "iv": [.3],
        "open_interest": [500], "volume": [20]})
    provider.preload_frame("NVDA", frame)
    quote = provider.get_quote("NVDA", "2024-01-03", "2024-02-02", 110.)
    assert quote["bid"] == 2.
    assert provider.lifecycle_storage_reads == 0
    assert provider.quote_cache_hits == 1


def test_episode_can_continue_across_year_boundary():
    e = CoveredCallEpisode("E1", "NVDA", 100, call("2024-12-20", "2025-01-10", 110, 2, 2.2))
    e.roll(call("2025-01-07", "2025-02-10", 115, 3, 3.2), old_buyback_price=1)
    assert e.close(close_date="2025-02-03", underlying_price=101, buyback_price=.5) > 0
    assert e.opened_date == "2024-12-20" and e.close_date == "2025-02-03"


def test_position_book_capacity_and_released_slot():
    book = CoveredCallPositionBook()
    episodes = [CoveredCallEpisode(str(i), "NVDA", 100, call("2024-12-20", "2025-01-10", 110, 2, 2.2)) for i in range(3)]
    for e in episodes:
        book.open(e)
    try:
        book.open(CoveredCallEpisode("4", "NVDA", 100, episodes[0].contract))
    except ValueError as exc:
        assert str(exc) == "MAX_CALL_CAPACITY_REACHED"
    book.close("0", close_date="2025-01-02", underlying_price=100, buyback_price=1)
    book.open(CoveredCallEpisode("4", "NVDA", 100, episodes[0].contract))


def test_roll_selector_allows_itm_and_uses_highest_strike():
    old = call("2025-01-01", "2025-01-10", 110, 2, 2.2)
    selector = CoveredCallRollSelector()
    selected = selector.select(old, "2025-01-07", 100, [
        call("2025-01-07", "2025-02-10", 95, 3, 3.2),
        call("2025-01-07", "2025-02-10", 120, 3, 3.2),
    ])
    assert selected[0].strike == 120


def test_roll_selector_rejects_debit_roll():
    old = call("2025-01-01", "2025-01-10", 110, 2, 2.2)
    assert CoveredCallRollSelector().select(old, "2025-01-07", 100,
        [call("2025-01-07", "2025-02-10", 95, 1, 1.2)]) is None


def test_roll_selector_requires_positive_fee_adjusted_credit():
    old = call("2025-01-01", "2025-01-10", 100, 2, 2.2)
    positive = call("2025-01-07", "2025-02-10", 101, 2.21, 2.4)
    assert CoveredCallRollSelector().select(old, "2025-01-07", 100, [positive])[0] == positive
    zero = call("2025-01-07", "2025-02-10", 101, 2, 2.4)
    assert CoveredCallRollSelector().select(old, "2025-01-07", 100, [zero]) is None


def test_episode_roll_uses_final_net_credit_after_both_leg_fees():
    old = call("2025-01-01", "2025-01-10", 100, 2, 2.2)
    new = call("2025-01-07", "2025-02-10", 101, 2.21, 2.4)
    e = CoveredCallEpisode("E1", "NVDA", 100, old)
    assert e.roll(new, old_buyback_price=2, close_leg_fees=10, open_leg_fees=10) == pytest.approx(1.0)
    for fees in ((10.5, 10.5), (11, 10)):
        e2 = CoveredCallEpisode("E2", "NVDA", 100, old)
        try:
            e2.roll(new, old_buyback_price=2, close_leg_fees=fees[0], open_leg_fees=fees[1])
        except ValueError as exc:
            assert str(exc) == "ROLL_REJECT_DEBIT"
        else:
            raise AssertionError("zero/debit fee-adjusted roll must be rejected")


def test_daily_engine_manages_before_new_entry_and_keeps_episode_id():
    old = call("2024-12-20", "2025-01-10", 110, 2, 2.2)
    new = call("2025-01-07", "2025-02-10", 115, 3, 3.2)
    result = CoveredCallDailyEngine("NVDA").run([
        {"date": "2024-12-20", "close": 100, "new_entry": True, "entry_contract": old},
        {"date": "2025-01-07", "close": 110},
    ], quotes_by_date={"2024-12-20": [old], "2025-01-07": [old, new]})
    assert [x["action"] for x in result["actions"]] == ["OPEN", "ROLL"]
    assert result["episodes"][0].episode_id == "NVDA-1"


def test_trigger_without_eligible_roll_holds_and_rechecks_next_session():
    old = call("2025-01-01", "2025-01-10", 100, 2, 2.2)
    result = CoveredCallDailyEngine("NVDA").run([
        {"date": "2025-01-01", "close": 100, "new_entry": True, "entry_contract": old},
        {"date": "2025-01-07", "close": 100},
        {"date": "2025-01-08", "close": 100},
    ], quotes_by_date={"2025-01-01": [old], "2025-01-07": [old], "2025-01-08": [old]})
    actions = result["actions"][-2:]
    assert [action["action"] for action in actions] == ["HOLD", "HOLD"]
    assert all(action["reason_codes"] == ["NO_ELIGIBLE_ROLL_RECHECK_NEXT_SESSION"] for action in actions)
    assert not result["episodes"][0].closed


def test_daily_engine_honors_close_grid_holding_and_remaining_dte_parameters():
    old = call("2024-12-20", "2025-01-10", 110, 2, 2.2)
    result = CoveredCallDailyEngine("NVDA", profit_capture=.60,
                                   minimum_holding_days=30,
                                   remaining_dte_condition=10).run([
        {"date": "2024-12-20", "close": 100, "new_entry": True, "entry_contract": old},
        {"date": "2024-12-27", "close": 100},
    ], quotes_by_date={"2024-12-20": [old], "2024-12-27": [old]})
    assert result["actions"][-1]["action"] == "HOLD"


def test_nvdl_itm_management_closes_before_assignment():
    old = CoveredCallContract("NVDL", "2025-01-02", "2025-02-14", 110, 2, 8,
                              open_interest=500, volume=20)
    result = CoveredCallDailyEngine("NVDL", close_when_itm=True).run([
        {"date": "2025-01-02", "close": 100, "new_entry": True, "entry_contract": old},
        {"date": "2025-01-03", "close": 111},
    ], quotes_by_date={"2025-01-02": [old], "2025-01-03": [old]})
    action = result["actions"][-1]
    assert action["action"] == "CLOSE"
    assert "NVDL_ITM_RISK" in action["reason_codes"]
