from pcs.research.covered_call import (
    CoveredCallContract, CoveredCallDailyEngine, CoveredCallEpisode,
    CoveredCallPositionBook, CoveredCallRollSelector,
)


def call(qdate, expiry, strike, bid, ask):
    return CoveredCallContract("NVDA", qdate, expiry, strike, bid, ask,
                               open_interest=500, volume=20)


def test_episode_roll_chain_preserves_accounting():
    e = CoveredCallEpisode("E1", "NVDA", 100, call("2024-12-20", "2025-01-10", 110, 2, 2.2))
    e.roll(call("2025-01-07", "2025-02-10", 115, 3, 3.2), old_buyback_price=1)
    assert e.episode_id == "E1"
    assert e.cumulative_premium_received == 520
    assert e.cumulative_buyback_cost == 100
    assert e.cumulative_roll_credits == 210
    assert e.close(close_date="2025-02-03", underlying_price=100, buyback_price=.5) == 370
    assert e.final_pnl == 370


def test_episode_can_continue_across_year_boundary():
    e = CoveredCallEpisode("E1", "NVDA", 100, call("2024-12-20", "2025-01-10", 110, 2, 2.2))
    e.roll(call("2025-01-07", "2025-02-10", 95, 3, 3.2), old_buyback_price=1)
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


def test_daily_engine_manages_before_new_entry_and_keeps_episode_id():
    old = call("2024-12-20", "2025-01-10", 110, 2, 2.2)
    new = call("2025-01-07", "2025-02-10", 95, 3, 3.2)
    result = CoveredCallDailyEngine("NVDA").run([
        {"date": "2024-12-20", "close": 100, "new_entry": True, "entry_contract": old},
        {"date": "2025-01-07", "close": 100},
    ], quotes_by_date={"2024-12-20": [old], "2025-01-07": [old, new]})
    assert [x["action"] for x in result["actions"]] == ["OPEN", "ROLL"]
    assert result["episodes"][0].episode_id == "NVDA-1"


def test_daily_engine_honors_close_grid_holding_and_remaining_dte_parameters():
    old = call("2024-12-20", "2025-01-10", 110, 2, 2.2)
    result = CoveredCallDailyEngine("NVDA", profit_capture=.60,
                                   minimum_holding_days=30,
                                   remaining_dte_condition=10).run([
        {"date": "2024-12-20", "close": 100, "new_entry": True, "entry_contract": old},
        {"date": "2024-12-27", "close": 100},
    ], quotes_by_date={"2024-12-20": [old], "2024-12-27": [old]})
    assert result["actions"][-1]["action"] == "HOLD"
