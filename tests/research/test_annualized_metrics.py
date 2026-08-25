import pandas as pd

from pcs.research.annualized_metrics import annualized_performance_metrics


def rows(pnls, start="2020-01-01", end=None):
    end = end or start
    return pd.DataFrame({"status": ["COMPLETE"] * len(pnls), "date": pd.date_range(start, periods=len(pnls), freq="180D"),
                        "exit_date": pd.date_range(start, periods=len(pnls), freq="180D"), "realized_pnl": pnls,
                        "planned_loss": [100] * len(pnls), "spread_width": [5] * len(pnls)})


def test_one_year_and_multi_year_cagr():
    one = annualized_performance_metrics(rows([100], end="2021-01-01"), starting_equity=1000, test_start_date="2020-01-01", test_end_date="2021-01-01")
    assert round(one["CAGR"], 6) == round(1.1 ** (365.25 / 366) - 1, 6)
    multi = annualized_performance_metrics(rows([200], end="2022-01-01"), starting_equity=1000, test_start_date="2020-01-01", test_end_date="2022-01-01")
    assert multi["test_days"] == 731


def test_partial_year_and_guards():
    result = annualized_performance_metrics(rows([50]), starting_equity=1000, test_start_date="2020-01-01", test_end_date="2020-07-01")
    assert result["CAGR"] is not None and result["test_days"] == 182
    assert annualized_performance_metrics(rows([1]), starting_equity=0, test_start_date="2020-01-01", test_end_date="2021-01-01")["CAGR"] is None
    assert annualized_performance_metrics(rows([1]), starting_equity=-1, test_start_date="2020-01-01", test_end_date="2021-01-01")["CAGR"] is None


def test_no_trade_and_capital_return_is_not_cagr():
    result = annualized_performance_metrics(rows([]), starting_equity=1000, test_start_date="2020-01-01", test_end_date="2021-01-01")
    assert result["trade_count"] == 0 and result["CAGR"] == 0
    result = annualized_performance_metrics(rows([100]), starting_equity=1000, test_start_date="2020-01-01", test_end_date="2021-01-01")
    assert result["CAGR"] != result["annualized_return_on_average_capital"]


def test_max_drawdown_is_invariant_to_input_order_for_same_exit_date():
    frame = pd.DataFrame([
        {"status": "COMPLETE", "candidate_id": "b", "date": "2020-01-02", "exit_date": "2020-01-10", "realized_pnl": -100},
        {"status": "COMPLETE", "candidate_id": "a", "date": "2020-01-02", "exit_date": "2020-01-10", "realized_pnl": 50},
        {"status": "COMPLETE", "candidate_id": "c", "date": "2020-01-03", "exit_date": "2020-01-11", "realized_pnl": 20},
    ])
    one = annualized_performance_metrics(frame, starting_equity=1000,
                                         test_start_date="2020-01-01", test_end_date="2020-01-11")
    two = annualized_performance_metrics(frame.sample(frac=1, random_state=7), starting_equity=1000,
                                         test_start_date="2020-01-01", test_end_date="2020-01-11")
    assert one["max_drawdown"] == two["max_drawdown"]
