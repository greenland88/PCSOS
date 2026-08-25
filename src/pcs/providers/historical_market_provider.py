from pcs.providers.base import HistoricalMarketProvider


class InMemoryHistoricalMarketProvider(HistoricalMarketProvider):
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def get_daily_ohlcv(self, symbols: list[str], start_date: str, end_date: str):
        wanted = set(symbols)
        return [
            r for r in self.rows
            if r["symbol"] in wanted and start_date <= r["date"] <= end_date
        ]
