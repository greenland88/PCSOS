"""Canonical schemas for derived local storage."""

OPTIONS_SCHEMA_VERSION = 1
DAILY_SCHEMA_VERSION = 1
OPTION_FIELDS = ["symbol", "trade_date", "expiration_date", "strike", "call_put", "last", "bid", "ask", "bid_iv", "ask_iv", "open_interest", "volume", "delta", "gamma", "vega", "theta", "rho"]
DAILY_FIELDS = ["symbol", "date", "open", "high", "low", "close", "volume"]
OPTIONS_REQUIRED_FIELDS = ["symbol", "trade_date", "expiration_date", "strike", "call_put"]
OPTIONS_OPTIONAL_FIELDS = [field for field in OPTION_FIELDS if field not in OPTIONS_REQUIRED_FIELDS] + ["underlying_price", "bid_size", "ask_size", "quote_time"]
DAILY_REQUIRED_FIELDS = list(DAILY_FIELDS)
DAILY_OPTIONAL_FIELDS = []
OPTIONS_SCHEMA_VERSIONS = {
    1: list(OPTION_FIELDS),
    2: list(OPTION_FIELDS) + ["underlying_price", "bid_size", "ask_size", "quote_time"],
}
DAILY_SCHEMA_VERSIONS = {1: list(DAILY_FIELDS)}


def option_schema_map():
    return {"Trade Date": "trade_date", "Expiry Date": "expiration_date", "Strike": "strike", "Call/Put": "call_put", "Last Trade Price": "last", "Bid Price": "bid", "Ask Price": "ask", "Bid Implied Volatility": "bid_iv", "Ask Implied Volatility": "ask_iv", "Open Interest": "open_interest", "Volume": "volume", "Delta": "delta", "Gamma": "gamma", "Vega": "vega", "Theta": "theta", "Rho": "rho"}
