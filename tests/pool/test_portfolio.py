from pcs.pool.final_gates import compose_final_action, evaluate_pool_portfolio
from pcs.pool.models import FinalAction, OptionsStatus, TimingStatus
from pcs.risk.portfolio_risk import PortfolioRiskSnapshot


def test_portfolio_limit_and_final_ready_require_all_gates():
    snap = PortfolioRiskSnapshot(100, 100, ticker_planned_loss={"AAA": 100})
    assert evaluate_pool_portfolio(snap, rules={"max_total_planned_loss": 50}).status == "PORTFOLIO_BLOCKED"
    action, _ = compose_final_action(timing_status=TimingStatus.TIMING_ENTRY_READY,
        options_status=OptionsStatus.PASS, event_status="EVENT_PASS", portfolio_status="PORTFOLIO_PASS")
    assert action == FinalAction.PCS_TRADE_READY


def test_timing_ready_alone_cannot_be_trade_ready():
    action, reasons = compose_final_action(timing_status=TimingStatus.TIMING_ENTRY_READY,
        options_status=OptionsStatus.NOT_EVALUATED, event_status="NOT_EVALUATED", portfolio_status="NOT_EVALUATED")
    assert action != FinalAction.PCS_TRADE_READY
    assert "OPTIONS_NOT_PASS" in reasons
