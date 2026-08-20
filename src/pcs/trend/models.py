REQUIRED_OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")

BASE_INDICATOR_COLUMNS = ("sma20", "sma50", "sma200", "atr14", "adx14", "rsi14")


class TrendIndicatorValidationError(ValueError):
    """Raised when OHLCV input or indicator output violates Trend Engine expectations."""
