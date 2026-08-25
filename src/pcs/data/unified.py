"""Compatibility facade for the canonical PCS data boundary."""
from .access import PCSDataAccess, SourceSpec


class UnifiedDataAccess(PCSDataAccess):
    """Legacy name delegating all reads to :class:`PCSDataAccess`."""

    def load_option_quotes(self, ticker, start_date, end_date, expirations=None, strikes=None):
        return self.read_quotes(ticker, start_date, end_date, expirations, strikes)

    def load_option_chain(self, ticker, trade_date, expiration=None):
        return self.read_option_chain(ticker, trade_date, expiration)


__all__ = ["SourceSpec", "PCSDataAccess", "UnifiedDataAccess"]
