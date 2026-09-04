"""Executable invariants for pool scan result accounting."""
from __future__ import annotations

from .models import OptionsStatus, TimingStatus, PoolScanResult


def validate_pool_result(result: PoolScanResult, expected_symbols) -> None:
    expected = [str(symbol).strip().upper() for symbol in expected_symbols]
    actual = [row.symbol for row in result.ticker_results]
    if len(actual) != len(set(actual)):
        raise ValueError("DUPLICATE_TICKER_DECISION")
    if set(actual) != set(expected):
        raise ValueError("MISSING_TICKER_DECISION")
    if actual != expected:
        raise ValueError("TICKER_DECISION_ORDER_MISMATCH")
    for row in result.ticker_results:
        if row.options_status != OptionsStatus.NOT_EVALUATED and row.timing_status != TimingStatus.TIMING_ENTRY_READY:
            raise ValueError("OPTIONS_EVALUATED_BEFORE_TIMING_READY")
    if result.summary.get("missing_ticker_decisions", 0) != 0:
        raise ValueError("MISSING_TICKER_DECISION")
