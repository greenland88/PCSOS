"""Intraday overlay that never mutates or recomputes daily timing."""
from __future__ import annotations

from dataclasses import dataclass

from .models import TimingStatus


@dataclass(frozen=True)
class ExecutionTimingSnapshot:
    symbol: str
    as_of: str
    timeframe: str
    status: str
    reason_codes: tuple[str, ...] = ()
    current_price: float | None = None


def run_intraday_overlay(daily_results, *, as_of: str, current_price_reader):
    """Build overlays for a frozen daily result set without daily recomputation."""
    return tuple(build_intraday_overlay(row, as_of=as_of,
                                        current_price_reader=current_price_reader)
                 for row in daily_results)


def build_intraday_overlay(daily_result, *, as_of: str, current_price_reader) -> ExecutionTimingSnapshot:
    """Refresh a hot pool ticker from current price only."""
    symbol = daily_result.symbol
    if daily_result.timing_status not in {TimingStatus.TIMING_ENTRY_READY, TimingStatus.WATCH}:
        return ExecutionTimingSnapshot(symbol, as_of, "intraday", "NOT_CHECKED",
                                       ("NOT_IN_INTRADAY_HOT_POOL",))
    try:
        price = float(current_price_reader(symbol))
    except Exception as exc:
        return ExecutionTimingSnapshot(symbol, as_of, "intraday", "DATA_BLOCKED",
                                       ("INTRADAY_PRICE_UNAVAILABLE", type(exc).__name__))
    return ExecutionTimingSnapshot(symbol, as_of, "intraday", "INTRADAY_PROVISIONAL",
                                   ("DAILY_STATE_PRESERVED",), price)
