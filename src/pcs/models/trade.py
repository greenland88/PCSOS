from pydantic import BaseModel
from typing import Any


class TradeCandidate(BaseModel):
    ticker: str
    expiration: str
    short_strike: float
    long_strike: float
    underlying_price: float
    credit: float
    dte: int
    short_delta: float
    expected_move: float
    support_level: float
    normal_daily_move: float
    option_volume: int
    open_interest: int
    bid_ask_pct: float
    nearby_strikes: int
    later_expirations: int
    business_quality: float
    trend_score: float
    support_score: float
    sector_alignment: float
    price_confirmation: float
    event_risk: int = 0
    correlation_bucket: str = "other"
    atr: float | None = None
    long_option_volume: int | None = None
    long_open_interest: int | None = None
    bid: float | None = None
    ask: float | None = None
    long_bid: float | None = None
    long_ask: float | None = None
    entry_date: str | None = None
    trend_snapshot: Any = None
    trend_interpretation: Any = None
    trend_score_result: Any = None
