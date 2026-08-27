import pandas as pd

from pcs.research.stage4a_production_universe import generate_structural_put_opportunities


def test_generator_constructs_exact_widths_without_decision_gates():
    chain = pd.DataFrame([
        {"Call/Put": "p", "Expiry Date": "2025-02-21", "Strike": 95.0, "Bid Price": 1.0, "Ask Price": 1.1, "Volume": 0, "Open Interest": 0, "Delta": -0.2},
        {"Call/Put": "p", "Expiry Date": "2025-02-21", "Strike": 100.0, "Bid Price": 2.0, "Ask Price": 2.1, "Volume": 0, "Open Interest": 0, "Delta": -0.3},
        {"Call/Put": "p", "Expiry Date": "2025-02-21", "Strike": 90.0, "Bid Price": 0.5, "Ask Price": 0.6, "Volume": 0, "Open Interest": 0, "Delta": -0.1},
    ])
    rows = generate_structural_put_opportunities(chain, "NVDA", "2025-01-10")
    assert {(r["short_strike"], r["long_strike"]) for r in rows} == {(100.0, 95.0), (100.0, 90.0), (95.0, 90.0)}
    assert all(r["option_type"] == "p" for r in rows)
