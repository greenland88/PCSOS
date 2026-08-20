from pydantic import BaseModel


class DailyOHLCV(BaseModel):
    date: str
    symbol: str
    open: float
    high: float
    low: float
    close: float
    adjusted_close: float | None = None
    volume: int | None = None


class LiveOptionContract(BaseModel):
    option_id: str
    ticker: str
    expiration: str
    strike: float
    option_type: str
    tradability: str = "tradable"
    multiplier: int = 100


class LiveOptionQuote(BaseModel):
    option_id: str
    ticker: str
    expiration: str
    strike: float
    option_type: str
    bid: float | None = None
    ask: float | None = None
    mark: float | None = None
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None
    open_interest: int | None = None
    volume: int | None = None
    probability_of_profit: float | None = None
    timestamp: str


class MarketFeature(BaseModel):
    date: str
    symbol: str
    price: float
    dma20: float | None = None
    dma50: float | None = None
    dma200: float | None = None
    dma20_slope: float | None = None
    dma50_slope: float | None = None
    atr5: float | None = None
    atr14: float | None = None
    realized_vol_20d: float | None = None
    drawdown: float | None = None
    trend_score: float | None = None
    predictability_score: float | None = None


class ExpectedMoveResult(BaseModel):
    distance_to_short_strike: float
    expected_move_1d: float
    expected_move_3d: float
    expected_move_5d: float
    expiration_expected_move: float | None = None
    buffer_ratio: float
    confidence: str = "ATR_REALIZED"


class RollCandidate(BaseModel):
    current_spread: str
    candidate_spread: str
    expiration: str
    net_credit_estimate: float | None = None
    days_added: int
    strike_improvement: float
    buffer_improvement: float | None = None
    liquidity_score: float
    rollability_score: float
