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


def test_structural_rejection_is_terminal():
    row = TickerScanResult("AAA", "r", "2025-01-01", EligibilityStatus.PCS_ELIGIBLE,
                           TimingStatus.WAIT, OptionsStatus.NOT_EVALUATED,
                           final_action=FinalAction.REJECTED,
                           reason_codes=("UNDERLYING_STRUCTURAL_REJECT",))
    result = finalize_ticker_result(row, event_status="EVENT_PASS", portfolio_status="PORTFOLIO_PASS")
    assert result.final_action == FinalAction.REJECTED
    assert "UNDERLYING_STRUCTURAL_REJECT" in result.reason_codes


def test_discovered_contracts_require_stage_c_selection():
    row = TickerScanResult("AAA", "r", "2025-01-01", EligibilityStatus.PCS_ELIGIBLE,
                           TimingStatus.TIMING_ENTRY_READY, OptionsStatus.DISCOVERED,
                           final_action=FinalAction.WAIT)
    result = finalize_ticker_result(row, event_status="EVENT_PASS", portfolio_status="PORTFOLIO_PASS")
    assert result.final_action == FinalAction.WAIT
    assert "CONTRACT_SELECTION_REQUIRED" in result.reason_codes
