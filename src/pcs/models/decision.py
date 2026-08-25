from enum import StrEnum
from pydantic import BaseModel, Field


class Action(StrEnum):
    OPEN = "OPEN"
    WAIT = "WAIT"
    HOLD = "HOLD"
    CLOSE = "CLOSE"
    ROLL = "ROLL"


class SizeClass(StrEnum):
    HALF = "0.5x"
    ONE = "1x"
    ONE_HALF = "1.5x"
    TWO = "2x"


class ScoreBreakdown(BaseModel):
    market_regime: float
    underlying_quality: float
    trend: float
    support: float
    liquidity: float
    rollability: float
    strike_buffer: float
    iv_premium: float
    portfolio_capacity: float
    news_risk: float


class Decision(BaseModel):
    ticker: str
    expiration: str
    short_strike: float
    long_strike: float
    underlying_price: float
    market_regime: str
    scores: ScoreBreakdown
    total_score: float
    classification: SizeClass
    action: Action
    reason: str
    recommended_contracts: int = 0
    estimated_credit: float = 0.0
    planned_risk: float = 0.0
    theoretical_max_loss: float = 0.0
    planned_loss: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)
    delta_diagnostics: dict = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    roll_candidate: dict | None = None
