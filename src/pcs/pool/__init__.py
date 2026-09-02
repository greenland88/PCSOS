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
from .final_gates import PoolEventResult, evaluate_pool_event
from .options import SpreadCandidate, shortlist_spreads

__all__ = [
    "EligibilityStatus", "FinalAction", "PoolRunSnapshot", "PoolScanResult",
    "TickerScanResult", "TimingStatus", "OptionsStatus",
    "run_pcs_pool",
    "PoolEventResult", "evaluate_pool_event",
    "SpreadCandidate", "shortlist_spreads",
]
