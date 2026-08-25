import math
from pydantic import BaseModel, Field, field_validator


class PCSPosition(BaseModel):
    ticker: str
    expiration: str
    short_strike: float
    long_strike: float
    underlying_price: float
    credit_opened: float = Field(ge=0)
    current_mark: float = Field(ge=0)
    contracts: int = Field(ge=0)
    dte: int
    planned_risk: float = Field(ge=0)
    theoretical_max_loss: float = Field(ge=0)
    support_level: float
    structure_valid: bool
    thesis_valid: bool
    liquidity_score: float
    rollability_score: float
    decline_temporary: bool = False
    candidate_roll: dict | None = None

    @field_validator("credit_opened", "current_mark", "planned_risk", "theoretical_max_loss")
    @classmethod
    def finite_nonnegative(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("POSITION_VALUE_MUST_BE_FINITE")
        return value

    @property
    def profit_capture_pct(self) -> float:
        if self.credit_opened <= 0:
            return 0.0
        return max(0.0, (self.credit_opened - self.current_mark) / self.credit_opened * 100)

