from pydantic import BaseModel


class OptionQuote(BaseModel):
    symbol: str
    expiration: str
    strike: float
    option_type: str
    bid: float
    ask: float
    mark: float
    delta: float
    volume: int
    open_interest: int
    implied_volatility: float


class OptionChain(BaseModel):
    symbol: str
    quotes: list[OptionQuote]

