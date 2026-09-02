"""Ticker-neutral PCS universe funnel interfaces."""

from .models import (
    EligibilityStatus,
    FinalAction,
    PoolRunSnapshot,
    PoolScanResult,
    TickerScanResult,
    TimingStatus,
    OptionsStatus,
)
from .runner import run_pcs_pool
from .final_gates import (PoolEventResult, evaluate_pool_event, PoolPortfolioResult,
                          evaluate_pool_portfolio, compose_final_action)
from .final_gates import finalize_ticker_result
from .options import SpreadCandidate, shortlist_spreads
from .validation import validate_pool_result
from .concurrency import WorkerOutcome, run_symbol_workers
from .modes import completed_daily_cutoff
from .intraday import ExecutionTimingSnapshot, build_intraday_overlay, run_intraday_overlay

__all__ = [
    "EligibilityStatus", "FinalAction", "PoolRunSnapshot", "PoolScanResult",
    "TickerScanResult", "TimingStatus", "OptionsStatus",
    "run_pcs_pool",
    "PoolEventResult", "evaluate_pool_event",
    "PoolPortfolioResult", "evaluate_pool_portfolio", "compose_final_action",
    "finalize_ticker_result",
    "SpreadCandidate", "shortlist_spreads",
    "validate_pool_result",
    "WorkerOutcome", "run_symbol_workers",
    "completed_daily_cutoff",
    "ExecutionTimingSnapshot", "build_intraday_overlay", "run_intraday_overlay",
]
