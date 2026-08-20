from pcs.research.defense_warning import eventual_outcome, first_warnings, recovery_after_warning, warning_flags


def test_outcome_and_first_warning():
    trade = {"events": {"stop": "2025-01-05", "profit50": None}}
    life = [{"date": "2025-01-01", "spread_multiple": 1.0, "relative_weakness": False, "semiconductor_weakness": False, "market_weakness": False},
            {"date": "2025-01-02", "spread_multiple": 1.5, "relative_weakness": True, "semiconductor_weakness": True, "market_weakness": False}]
    assert eventual_outcome(trade) == "STOP"
    first = first_warnings(life)
    assert first["spread_deterioration"]["date"] == "2025-01-02"
    assert first["relative_plus_semiconductor"]["date"] == "2025-01-02"


def test_no_lookahead_and_recovery():
    life = [{"date": "2025-01-01", "spread_multiple": 1.5}, {"date": "2025-01-03", "spread_multiple": .4}]
    assert recovery_after_warning(life, "2025-01-01", 1.0)["recovery_days"] == 2
    assert not warning_flags({"spread_multiple": 1.0, "relative_weakness": False, "semiconductor_weakness": False, "market_weakness": False})["spread_deterioration"]
