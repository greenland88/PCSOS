from pcs.risk.portfolio_risk import PortfolioRiskAggregator, summarize_portfolio


def test_missing_account_capital_is_unknown_not_zero_percent():
    snapshot = PortfolioRiskAggregator().snapshot([])
    assert snapshot.account_capital is None
    assert snapshot.account_pct_simultaneous_planned_loss is None
    assert summarize_portfolio([])["account_pct_simultaneous_planned_loss"] is None


def test_explicit_account_capital_computes_percentage():
    snapshot = PortfolioRiskAggregator().snapshot([], pending_planned_loss=250.0, account_capital=10_000.0)
    assert snapshot.account_capital == 10_000.0
    assert snapshot.account_pct_simultaneous_planned_loss == 2.5
