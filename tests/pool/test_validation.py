import pytest

from pcs.pool.models import (EligibilityStatus, OptionsStatus, PoolRunSnapshot,
                             PoolScanResult, TickerScanResult, TimingStatus)
from pcs.pool.validation import validate_pool_result


def _result(rows):
    snap = PoolRunSnapshot("r", "2025-01-01", "EOD", None, "u")
    return PoolScanResult(snap, tuple(rows), {"missing_ticker_decisions": 0})


def test_validation_rejects_missing_or_duplicate_decisions():
    row = TickerScanResult("AAA", "r", "2025-01-01", EligibilityStatus.PCS_ELIGIBLE)
    with pytest.raises(ValueError, match="MISSING_TICKER_DECISION"):
        validate_pool_result(_result([row]), ["AAA", "BBB"])
    with pytest.raises(ValueError, match="DUPLICATE_TICKER_DECISION"):
        validate_pool_result(_result([row, row]), ["AAA"])


def test_validation_rejects_options_before_timing():
    row = TickerScanResult("AAA", "r", "2025-01-01", EligibilityStatus.PCS_ELIGIBLE,
                           timing_status=TimingStatus.WATCH, options_status=OptionsStatus.PASS)
    with pytest.raises(ValueError, match="OPTIONS_EVALUATED_BEFORE_TIMING_READY"):
        validate_pool_result(_result([row]), ["AAA"])
