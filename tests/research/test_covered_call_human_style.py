import pandas as pd

from pcs.research.covered_call_human_style import (
    HumanStyleConfig, _select_entry, _signal, audit_mechanical_configurations,
)


def config(**updates):
    values = dict(config_id="T", family="LIMITED_ROLL", dte_min=14, dte_max=30,
                  delta_min=.10, delta_max=.20, profit_take=.50,
                  minimum_moneyness=.05, minimum_premium_yield=.005,
                  cooldown_days=10, max_rolls=1, defense_budget_ratio=.50)
    values.update(updates)
    return HumanStyleConfig(**values)


def test_default_wait_blocks_breakout_even_with_iv_and_rally():
    row = {"date": "2023-01-03", "feature_ready": True, "breakout": True,
           "trend_acceleration": False, "iv_rank_126": .9, "iv_change_5d": .1,
           "return_5d": .10, "rally": True, "resistance_near": True}
    allowed, reasons = _signal(row, config(), set(), 7)
    assert allowed is False
    assert "STRONG_BREAKOUT" in reasons


def test_entry_selector_enforces_liquidity_premium_delta_and_far_otm():
    quotes = pd.DataFrame([
        {"dte": 20, "delta_abs": .15, "open_interest": 200, "volume": 5,
         "spread_pct": .10, "expiration_date": pd.Timestamp("2023-01-23"),
         "strike": 110.0, "bid": 1.0},
        {"dte": 20, "delta_abs": .15, "open_interest": 1, "volume": 5,
         "spread_pct": .10, "expiration_date": pd.Timestamp("2023-01-23"),
         "strike": 115.0, "bid": 2.0},
    ])
    row = {"close": 100.0, "prior_high60": 105.0}
    selected = _select_entry(quotes, row, config(), pd.Timestamp("2023-12-31"))
    assert float(selected.strike) == 110.0


def test_mechanical_audit_supersedes_incomplete_matrix():
    rows = [{"config_id": "x", "entry_timing": "RESISTANCE_NEAR", "target_delta": .10,
             "dte_min": 14, "dte_max": 30, "profit_target": .50}]
    result = audit_mechanical_configurations(rows)
    assert result["audited_config_count"] == 1
    assert result["prior_candidate_status"] == "SUPERSEDED_BEFORE_HOLDOUT"
    assert result["behaviors"][0]["tested"] == "NO"
