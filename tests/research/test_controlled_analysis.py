from copy import deepcopy

from pcs.research.controlled_analysis import (
    atr_bucket, credit_bucket, dte_bucket, group_metrics, matched_aggregate,
)


def test_bucket_boundaries():
    assert dte_bucket(20) == "20-29"
    assert dte_bucket(29) == "20-29"
    assert dte_bucket(30) == "30-39"
    assert dte_bucket(45) == "40-45"
    assert atr_bucket(1.75) == "<=1.75"
    assert atr_bucket(2.25) == "1.76-2.25"
    assert credit_bucket(.20) == "15-20%"
    assert credit_bucket(.30) == "21-30%"


def test_metrics_and_profit_factor():
    rows = [{"realized_pnl": 20, "profit50_before_stop": True, "profit70_before_stop": True,
             "stop_before_profit50": False, "days_held": 3},
            {"realized_pnl": -10, "profit50_before_stop": False, "profit70_before_stop": False,
             "stop_before_profit50": True, "days_held": 2}]
    result = group_metrics(rows)
    assert result["profit_factor"] == 2
    assert result["average_stop_loss"] == -10
    assert result["worst_trade"] == -10


def test_group_metrics_does_not_mutate_rows():
    rows = [{"realized_pnl": 1, "profit50_before_stop": True, "profit70_before_stop": True,
             "stop_before_profit50": False, "days_held": 1}]
    original = deepcopy(rows)
    group_metrics(rows)
    assert rows == original
