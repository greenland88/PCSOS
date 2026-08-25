from enum import StrEnum
from pydantic import BaseModel


class Regime(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class MarketState(BaseModel):
    # Missing market confirmation must never silently become a healthy regime.
    qqq_above_20dma: bool = False
    qqq_above_50dma: bool = False
    qqq_above_200dma: bool = False
    spy_above_50dma: bool = False
    soxx_above_50dma: bool = False
    # Legacy field name. Current contract is SPY_QQQ_MARKET_CONFIRMATION:
    # (SPY close > SPY SMA50) AND (QQQ close > QQQ SMA50).
    breadth_positive: bool = False
    vix: float | None = None
    recent_drawdown_pct: float = 0.0
    sharp_selloff: bool = False

