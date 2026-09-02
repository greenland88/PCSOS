from pcs.pool.intraday import build_intraday_overlay
from pcs.pool.models import EligibilityStatus, TickerScanResult, TimingStatus


def test_intraday_overlay_is_provisional_and_preserves_daily_state():
    row = TickerScanResult("AAA", "r", "2025-01-01", EligibilityStatus.PCS_ELIGIBLE,
                           TimingStatus.WATCH)
    overlay = build_intraday_overlay(row, as_of="2025-01-02T10:00:00", current_price_reader=lambda _: 101)
    assert overlay.status == "INTRADAY_PROVISIONAL"
    assert overlay.timeframe == "intraday"
    assert "DAILY_STATE_PRESERVED" in overlay.reason_codes


def test_non_hot_ticker_does_not_read_price():
    row = TickerScanResult("AAA", "r", "2025-01-01", EligibilityStatus.PCS_ELIGIBLE,
                           TimingStatus.DORMANT)
    def fail(_):
        raise AssertionError("reader must not be called")
    assert build_intraday_overlay(row, as_of="2025-01-02", current_price_reader=fail).status == "NOT_CHECKED"
