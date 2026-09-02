from pcs.pool.final_gates import finalize_ticker_result
from pcs.pool.models import EligibilityStatus, FinalAction, OptionsStatus, TickerScanResult, TimingStatus


def test_finalizer_requires_all_gates():
    row = TickerScanResult("AAA", "r", "2025-01-01", EligibilityStatus.PCS_ELIGIBLE,
                           TimingStatus.TIMING_ENTRY_READY, OptionsStatus.PASS,
                           final_action=FinalAction.WAIT)
    blocked = finalize_ticker_result(row, event_status="EVENT_BLOCKED", portfolio_status="PORTFOLIO_PASS")
    assert blocked.final_action == FinalAction.TEMP_BLOCKED
    ready = finalize_ticker_result(row, event_status="EVENT_PASS", portfolio_status="PORTFOLIO_PASS")
    assert ready.final_action == FinalAction.PCS_TRADE_READY
