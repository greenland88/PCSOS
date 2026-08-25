from pydantic import BaseModel


class PCSPosition(BaseModel):
    ticker: str
    expiration: str
    short_strike: float
    long_strike: float
    underlying_price: float
    credit_opened: float
    current_mark: float
    contracts: int
    dte: int
    planned_risk: float
    theoretical_max_loss: float
    support_level: float
    structure_valid: bool
    thesis_valid: bool
    liquidity_score: float
    rollability_score: float
    decline_temporary: bool = False
    candidate_roll: dict | None = None

    @property
    def profit_capture_pct(self) -> float:
        if self.credit_opened <= 0:
            return 0.0
        return max(0.0, (self.credit_opened - self.current_mark) / self.credit_opened * 100)

