from enum import StrEnum
from pydantic import BaseModel


class Regime(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class MarketState(BaseModel):
    qqq_above_20dma: bool = True
    qqq_above_50dma: bool = True
    qqq_above_200dma: bool = True
    spy_above_50dma: bool = True
    soxx_above_50dma: bool = True
    # Legacy field name. Current contract is SPY_QQQ_MARKET_CONFIRMATION:
    # (SPY close > SPY SMA50) AND (QQQ close > QQQ SMA50).
    breadth_positive: bool = True
    vix: float | None = None
    recent_drawdown_pct: float = 0.0
    sharp_selloff: bool = False

