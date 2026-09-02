from pcs.pool.intraday import run_intraday_overlay
from pcs.pool.models import EligibilityStatus, TickerScanResult, TimingStatus


def test_batch_overlay_preserves_symbol_order():
    rows = [TickerScanResult("BBB", "r", "2025-01-01", EligibilityStatus.PCS_ELIGIBLE, TimingStatus.WATCH),
            TickerScanResult("AAA", "r", "2025-01-01", EligibilityStatus.PCS_ELIGIBLE, TimingStatus.DORMANT)]
    result = run_intraday_overlay(rows, as_of="2025-01-02", current_price_reader=lambda _: 10)
    assert [row.symbol for row in result] == ["BBB", "AAA"]
    assert result[1].status == "NOT_CHECKED"
