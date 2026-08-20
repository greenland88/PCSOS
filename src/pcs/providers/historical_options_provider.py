from pcs.providers.base import HistoricalOptionsProvider


class EmptyHistoricalOptionsProvider(HistoricalOptionsProvider):
    def get_option_history(self, symbol: str, start_date: str, end_date: str):
        return {
            "data_tier": "UNAVAILABLE",
            "confidence": "NONE",
            "rows": [],
            "message": "Historical options are not available from this provider.",
        }
